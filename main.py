import threading
import logging
from datetime import datetime

from dotenv import load_dotenv
import os
load_dotenv()

from flask import Flask, request, jsonify, abort, send_file
from flask_cors import CORS
import pybit
from pybit.unified_trading import HTTP

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Bybit API — Testnet ────────────────────────────────────────────────────────
BYBIT_LONG_API_KEY    = os.getenv("BYBIT_LONG_API_KEY")
BYBIT_LONG_API_SECRET = os.getenv("BYBIT_LONG_API_SECRET")
BYBIT_SHORT_API_KEY   = os.getenv("BYBIT_SHORT_API_KEY")
BYBIT_SHORT_API_SECRET = os.getenv("BYBIT_SHORT_API_SECRET")

# Testnet=True → gerçek para kullanılmaz. Canlıya geçince False yap.
client_long  = HTTP(testnet=True, api_key=BYBIT_LONG_API_KEY,  api_secret=BYBIT_LONG_API_SECRET)
client_short = HTTP(testnet=True, api_key=BYBIT_SHORT_API_KEY, api_secret=BYBIT_SHORT_API_SECRET)

# ── Global state ───────────────────────────────────────────────────────────────
state_lock   = threading.Lock()

# Bekleyen sinyaller — her 4 saatlik kapanışta birikir, sonra işleme alınır
pending_signals = []

# Dashboard için son durum
last_status = {
    "timestamp": None,
    "processed": [],   # işleme alınan sinyaller
    "skipped": [],     # atlanan sinyaller (neden atlandığı ile)
    "open_positions": []
}

# ── Bybit yardımcı fonksiyonlar ────────────────────────────────────────────────

def get_open_positions(client, symbol):
    """Bir coinin açık pozisyon sayısı ve toplam tutarını döner."""
    try:
        resp = client.get_positions(category="linear", symbol=symbol)
        positions = resp.get("result", {}).get("list", [])
        count = 0
        total_used = 0.0
        for p in positions:
            size = float(p.get("size", 0))
            avg_price = float(p.get("avgPrice", 0))
            if size > 0 and avg_price > 0:
                count += 1
                total_used += size * avg_price
        log.info(f"{symbol} pozisyon sorgusu → toplam kullanılan: {total_used}")
        log.info(f"Pozisyon detay: size={p.get('size')} avgPrice={p.get('avgPrice')} symbol={p.get('symbol')}")
        return count, total_used
    except Exception as e:
        log.error(f"Pozisyon sorgu hatası ({symbol}): {e}")
        return 0, 0.0

def get_all_positions(client):
    """Tüm açık pozisyonları çeker."""
    try:
        resp = client.get_positions(category="linear", settleCoin="USDT")
        positions = resp.get("result", {}).get("list", [])
        result = []
        for p in positions:
            size = float(p.get("size", 0))
            if size > 0:
                result.append({
                    "symbol": p.get("symbol"),
                    "direction": "Long" if p.get("side") == "Buy" else "Short",
                    "size": size,
                    "avg_price": float(p.get("avgPrice", 0)),
                    "unrealized_pnl": float(p.get("unrealisedPnl", 0)),
                    "sl": p.get("stopLoss"),
                    "tp": p.get("takeProfit"),
                })
        return result
    except Exception as e:
        log.error(f"Pozisyon listesi hatası: {e}")
        log.info(f"Short pozisyon sorgusu sonucu: {positions}")
        return []

def set_leverage(client, symbol, leverage):
    """Kaldıraç ayarla."""
    try:
        client.set_leverage(
            category="linear",
            symbol=symbol,
            buyLeverage=str(leverage),
            sellLeverage=str(leverage)
        )
    except Exception as e:
        log.warning(f"Kaldıraç ayar hatası ({symbol}): {e}")

def get_qty_step(client, symbol):
    try:
        resp = client.get_instruments_info(category="linear", symbol=symbol)
        lot_filter = resp["result"]["list"][0]["lotSizeFilter"]
        step = float(lot_filter["qtyStep"])
        min_qty = float(lot_filter["minOrderQty"])
        return step, min_qty
    except Exception as e:
        log.warning(f"Lot size alınamadı ({symbol}): {e}")
        return 0.001, 0.001

def round_qty(qty, step):
    import math
    decimals = len(str(step).rstrip('0').split('.')[-1]) if '.' in str(step) else 0
    qty = math.floor(qty / step) * step
    return round(qty, decimals)

def place_order(client, symbol, direction, amount, price, leverage, sl_pct, tp_pct):
    """
    Bybit'te işlem aç.
    amount  = dolar cinsinden tutar
    price   = giriş fiyatı
    sl_pct  = stop loss yüzdesi
    tp_pct  = take profit yüzdesi
    """
    # Sembol 1000 ile başlıyorsa fiyatı 1000 ile çarp
    #if symbol.startswith("1000"):
     #   price = price * 1000

     # Bybit'ten anlık fiyatı çek
    ticker = client.get_tickers(category="linear", symbol=symbol)
    price = float(ticker["result"]["list"][0]["lastPrice"])

    side = "Buy" if direction == "Long" else "Sell"
    step, min_qty = get_qty_step(client, symbol)
    qty = round_qty(amount / price, step)
    if qty < min_qty:
        qty = min_qty

    # SL ve TP fiyatları
    if direction == "Long":
        sl_price = round(price * (1 - sl_pct / 100), 6)
        tp_price = round(price * (1 + tp_pct / 100), 6)
    else:
        sl_price = round(price * (1 + sl_pct / 100), 6)
        tp_price = round(price * (1 - tp_pct / 100), 6)

    set_leverage(client, symbol, leverage)

    try:
        resp = client.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            stopLoss=str(sl_price),
            takeProfit=str(tp_price),
            timeInForce="GTC"
        )
        log.info(f"İşlem açıldı: {symbol} {direction} | Miktar:{qty} | SL:{sl_price} | TP:{tp_price}")
        return True, resp
    except Exception as e:
        log.error(f"İşlem açma hatası ({symbol}): {e}")
        return False, str(e)


# ── Sinyal işleme mantığı ──────────────────────────────────────────────────────

def process_signals(signals):
    """
    Gelen sinyalleri önceliklendirip Bybit'te işlem açar.

    Sıralama kuralları:
    1. total_amount büyük olan önce
    2. Aynı total_amount içinde fiyatı küçük olan önce
    3. Aynı coin gelirse priority yüksek olan seçilir
    """
    processed = []
    skipped   = []

    # Aynı coin birden fazla geldiyse önceliği yüksek olanı tut
    deduped = {}
    for sig in signals:
        sym = sig["symbol"]
        if sym not in deduped:
            deduped[sym] = sig
        else:
            existing = deduped[sym]
            if sig["priority"] > existing["priority"]:
                log.info(f"{sym}: Öncelik {sig['priority']} > {existing['priority']}, sinyal güncellendi")
                deduped[sym] = sig
            else:
                log.info(f"{sym}: Öncelik {sig['priority']} <= {existing['priority']}, atlandı")

    # Sırala: total_amount büyük → fiyat küçük
    sorted_signals = sorted(
        deduped.values(),
        key=lambda s: (-s["total_amount"], s["price"])
    )

    for sig in sorted_signals:
        symbol       = sig["symbol"]
        direction    = sig["direction"]
        price        = sig["price"]
        amount       = sig["amount"]
        leverage     = sig["leverage"]
        sl_pct       = sig["sl"]
        tp_pct       = sig["tp"]
        total_amount = sig["total_amount"]

        client = client_long if direction == "Long" else client_short

        # Açık pozisyon kontrolü
        _, total_used = get_open_positions(client, symbol)
        if total_used + amount > total_amount:
            reason = f"Limit aşılır ({total_used:.0f}+{amount} > {total_amount})"
            log.info(f"{symbol} atlandı: {reason}")
            skipped.append({**sig, "reason": reason})
            continue

        # İşlemi aç
        success, resp = place_order(client, symbol, direction, amount, price, leverage, sl_pct, tp_pct)
        if success:
            processed.append({**sig, "sl_price": round(price*(1-sl_pct/100),6) if direction=="Long" else round(price*(1+sl_pct/100),6),
                               "tp_price": round(price*(1+tp_pct/100),6) if direction=="Long" else round(price*(1-tp_pct/100),6)})
        else:
            skipped.append({**sig, "reason": str(resp)})

    return processed, skipped


# ── Güvenlik ───────────────────────────────────────────────────────────────────

@app.before_request
def limit_remote_addr():
    # TEST MODU: IP kontrolü kapalı.
    # Production'a geçince aşağıdaki satırların başındaki # kaldır:
    # trusted_ips = ["52.89.214.238","34.212.75.30","54.218.53.128","52.32.178.7"]
    # if request.remote_addr not in trusted_ips:
    #     abort(403)
    pass


# ── Sinyal endpoint ────────────────────────────────────────────────────────────

@app.route('/signal', methods=['POST'])
def receive_signal():
    """
    TradingView'den gelen sinyal.
    Beklenen format:
    {
        "symbol": "LUNC",
        "price": "0.01020200",
        "direction": "Long",
        "priority": 2,
        "total_amount": 1000,
        "amount": 100,
        "leverage": 25,
        "sl": 16,
        "tp": 8
    }
    """
    global pending_signals, last_status

    try:
        data = request.get_json(force=True)
        if data is None:
            raise ValueError("JSON parse hatası")
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    required = ["symbol", "price", "direction", "priority", "total_amount", "amount", "leverage", "sl", "tp"]
    for field in required:
        if field not in data:
            return jsonify({"error": f"{field} alanı eksik"}), 400

    try:
        signal = {
            "symbol":       data["symbol"],
            "price":        float(data["price"]),
            "direction":    data["direction"],
            "priority":     int(data["priority"]),
            "total_amount": float(data["total_amount"]),
            "amount":       float(data["amount"]),
            "leverage":     int(data["leverage"]),
            "sl":           float(data["sl"]),
            "tp":           float(data["tp"]),
            "timestamp":    datetime.utcnow().isoformat()
        }
    except (TypeError, ValueError) as e:
        return jsonify({"error": f"Geçersiz değer: {e}"}), 400

    if signal["direction"] not in ["Long", "Short"]:
        return jsonify({"error": "direction 'Long' veya 'Short' olmalı"}), 400

    log.info(f"Sinyal alındı: {signal['symbol']} {signal['direction']} | Fiyat:{signal['price']} | Öncelik:{signal['priority']}")

    with state_lock:
        pending_signals.append(signal)
        processed, skipped = process_signals(pending_signals)
        pending_signals = []

        last_status = {
            "timestamp": datetime.utcnow().isoformat(),
            "processed": processed,
            "skipped": skipped,
            "open_positions": []
        }

    return jsonify({
        "message": "Sinyal işlendi",
        "processed": len(processed),
        "skipped": len(skipped)
    }), 200


# ── Dashboard endpoint'leri ────────────────────────────────────────────────────

@app.route('/status', methods=['GET'])
def get_status():
    long_positions  = get_all_positions(client_long)
    short_positions = get_all_positions(client_short)
    with state_lock:
        data = dict(last_status)
    data["long_positions"]  = long_positions
    data["short_positions"] = short_positions
    return jsonify(data), 200


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200


@app.route('/dashboard', methods=['GET'])
def dashboard():
    return send_file('dashboard.html')


# ── Başlatma ───────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    CORS(app)
    app.run(host='0.0.0.0', port=5001, debug=False)
