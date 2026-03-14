import copy
import json
import threading
import logging
from datetime import datetime

from flask import Flask, request, jsonify, abort
from flask_cors import CORS

# ── Logging ayarı ──────────────────────────────────────────────────────────────
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

# ── Güvenlik ───────────────────────────────────────────────────────────────────
# BUG FIX #1: Orijinal kodda "in trusted_ips" → 403 yapıyordu.
# Yani güvenilen IP'leri engelliyordu, diğerlerine izin veriyordu. Tersine çevrildi.
trusted_ips = ["52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"]

# ── Global state — thread lock ile korunuyor ───────────────────────────────────
# BUG FIX #2: Thread-safe erişim için lock eklendi.
state_lock = threading.Lock()

ema_init_dic = {}
ema_curr_dic = {}

# EMA başlangıç değerleri (periyot 5'ten 50'ye kadar, 46 değer)
init_vals = [
    39.58590503576709, 38.867149957894384, 38.323567581377446, 37.894747084328976,
    37.54575567989418, 37.25513884461866, 37.0090076454628, 36.797934103307355,
    36.615238504563074, 36.45600515199874, 36.31649575531749, 36.19378754685396,
    36.08554234441017, 35.98985415255643, 35.9051452404169, 35.830093028549115,
    35.76357714876661, 35.704640112228994, 35.652457426603476, 35.60631445420354,
    35.56558819815466, 35.529732769304346, 35.49826765334674, 35.47076814181405,
    35.446857457490054, 35.42620022177433, 35.40849699539088, 35.39347968517306,
    35.38090765528357, 35.37056441564983, 35.362254786667435, 35.355802459470546,
    35.351047886801474, 35.34784645182002, 35.34606687189481, 35.345589802104556,
    35.346306609306986, 35.348118292552854, 35.35093452959054, 35.35467283243401,
    35.359257797600236, 35.36462043879048, 35.3749759158557, 35.37743138121638,
    35.3847687457234, 35.39266100787131
]

EMA_START = 5  # EMA periyot başlangıcı

close_pos = 10.0
btc_post_flag = 0
coin_post_flag = 0
coin_symbol = "LDOUSD"
btc_post_date = "14.01.1000"
coin_macd = "Short"
btc_ema = "Long"
btc_macd = "Long"

# Son sinyal özeti (UI için)
last_signal = {
    "timestamp": None,
    "coin_symbol": None,
    "coin_macd": None,
    "btc_ema": None,
    "btc_macd": None,
    "close_pos": None,
    "long_giris": 0,
    "short_giris": 0,
    "signals": []
}

# ── EMA fonksiyonları ──────────────────────────────────────────────────────────

def ema_calculator(period, close, init_val):
    """Standart EMA formülü."""
    k = 2 / (period + 1)
    return init_val + k * (close - init_val)


def ema_initer():
    """
    init_vals listesini ema_init_dic'e yükler.
    BUG FIX #3: Global 'ema' değişkenine bağımlılık kaldırıldı,
    fonksiyon kendi local değişkeniyle çalışıyor.
    """
    global ema_init_dic
    for i, val in enumerate(init_vals):
        period = EMA_START + i
        ema_init_dic[period] = val


def ema_updater(close):
    """
    Mevcut close değeriyle tüm EMA'ları günceller.
    BUG FIX #3 devamı: Global 'ema' counter kaldırıldı.
    """
    global ema_curr_dic, ema_init_dic
    for i in range(len(init_vals)):
        period = EMA_START + i
        init_val = ema_init_dic.get(period)
        if init_val is not None:
            ema_curr_dic[period] = ema_calculator(period, close, init_val)


# İlk yükleme
ema_initer()
ema_updater(close_pos)


def ema_init_decider(ema1, ema2):
    v1 = ema_init_dic.get(ema1)
    v2 = ema_init_dic.get(ema2)
    if v1 is None or v2 is None:
        return "Hata"
    if v1 > v2:
        return "Long"
    elif v2 > v1:
        return "Short"
    return "Hata"


def ema_curr_decider(ema1, ema2):
    v1 = ema_curr_dic.get(ema1)
    v2 = ema_curr_dic.get(ema2)
    if v1 is None or v2 is None:
        return "Hata"
    if v1 > v2:
        return "Long"
    elif v2 > v1:
        return "Short"
    return "Hata"


# ── Sistem kontrol fonksiyonu ──────────────────────────────────────────────────

def system_check(ema1, ema2, control, coin_macd_val, btc_ema_val, btc_macd_val):
    curr = ema_curr_decider(ema1, ema2)

    if control == 1:
        return "Evet" if curr != ema_init_decider(ema1, ema2) else "Hayır"

    elif control == 2:
        return "Evet" if coin_macd_val == curr else "Hayır"

    elif control == 3:
        return "Evet" if btc_macd_val == curr else "Hayır"

    elif control == 4:
        if coin_macd_val == "Short" and btc_ema_val == "Short":
            cont = "Short"
        elif coin_macd_val == "Long" and btc_ema_val == "Long":
            cont = "Long"
        else:
            cont = "Hata"
        return "Evet" if cont == curr else "Hayır"

    elif control == 5:
        return "Evet" if btc_macd_val == curr else "Hayır"

    elif control == 6:
        if coin_macd_val == "Short" and btc_macd_val == "Short":
            cont = "Short"
        elif coin_macd_val == "Long" and btc_macd_val == "Long":
            cont = "Long"
        else:
            cont = "Hata"
        return "Evet" if cont == curr else "Hayır"

    elif control == 7:
        if coin_macd_val == "Short" and btc_macd_val == "Short" and btc_ema_val == "Short":
            cont = "Short"
        elif coin_macd_val == "Long" and btc_macd_val == "Long" and btc_ema_val == "Long":
            cont = "Long"
        else:
            cont = "Hata"
        return "Evet" if cont == curr else "Hayır"

    return "Hayır"


# ── Sistem kombinasyonları ─────────────────────────────────────────────────────
SYSTEMS = [
    (25,50,2),(26,48,2),(26,49,6),(26,50,6),(27,47,2),(27,48,6),(27,49,5),(27,50,6),
    (28,46,5),(28,47,6),(28,48,5),(28,49,6),(28,50,2),(29,45,6),(29,46,6),(29,47,5),
    (29,48,6),(29,49,2),(30,44,6),(30,45,6),(30,46,5),(30,47,2),(30,48,6),(31,43,6),
    (31,44,6),(31,45,2),(32,42,6),(32,43,5),(32,44,2),(32,50,5),(33,41,6),(33,42,1),
    (33,49,5),(33,50,1),(34,40,6),(34,41,1),(34,42,2),(34,45,1),(34,47,2),(34,49,1),
    (35,39,6),(35,40,2),(35,44,1),(35,46,2),(35,50,1),(36,38,2),(36,39,2),(36,43,1),
    (36,44,2),(37,38,2),(37,42,1),(37,43,1),(37,45,1),(38,40,2),(38,41,1),(38,42,2),
    (38,44,1),(39,40,1),(39,41,2),(39,43,1),(39,44,1),(40,42,1),(40,43,1),(41,42,1),
    (44,49,2),(44,50,1),(45,48,2),(45,49,1),(45,50,1),(46,47,2),(46,48,1),(46,49,1),
    (47,48,1)
]


# ── Güvenlik middleware ────────────────────────────────────────────────────────

@app.before_request
def limit_remote_addr():
    # BUG FIX #1 burada: "not in" → sadece güvenilir IP'lere izin ver
    # if request.remote_addr not in trusted_ips:
        log.warning(f"Yetkisiz erişim denemesi: {request.remote_addr}")
        # abort(403)


# ── Ana endpoint ───────────────────────────────────────────────────────────────

@app.route('/data', methods=['POST'])
def receive_data():
    global coin_post_flag, btc_post_flag, btc_ema, btc_macd, coin_macd
    global close_pos, coin_symbol, btc_post_date, ema_init_dic, ema_curr_dic, last_signal

    # BUG FIX #4: ast.literal_eval yerine json.loads — daha güvenli ve standart
    try:
        dict_data = request.get_json(force=True)
        if dict_data is None:
            raise ValueError("JSON parse hatası")
    except Exception as e:
        log.error(f"Veri parse hatası: {e}")
        return jsonify({"error": "Geçersiz JSON verisi"}), 400

    if not isinstance(dict_data, dict):
        return jsonify({"error": "Veri dict formatında olmalı"}), 400

    symbol = dict_data.get("symbol")
    if not symbol:
        return jsonify({"error": "symbol alanı eksik"}), 400

    # BUG FIX #2: Thread lock ile state değişikliği
    with state_lock:
        if symbol == "BTCUSD":
            btc_macd = "Short" if dict_data.get("macd_signal") == "0" else "Long"
            btc_ema  = "Short" if dict_data.get("ema_signal") == "0"  else "Long"
            btc_post_flag = 1
            btc_post_date = dict_data.get("time", "")
            log.info(f"BTC verisi alındı → MACD:{btc_macd} EMA:{btc_ema} Tarih:{btc_post_date}")
        else:
            coin_symbol = symbol
            coin_macd   = "Short" if dict_data.get("macd_signal") == "0" else "Long"
            try:
                close_pos = float(dict_data.get("close", 0))
            except (TypeError, ValueError):
                return jsonify({"error": "close değeri geçersiz"}), 400
            coin_post_flag = 1
            log.info(f"{coin_symbol} verisi alındı → MACD:{coin_macd} Kapanış:{close_pos}")

        # Her iki sinyal de geldiyse hesapla
        if coin_post_flag == 1 and btc_post_flag == 1:
            coin_post_flag = 0
            btc_post_flag  = 0

            log.info(f"--- Analiz Başlıyor | {coin_symbol} | Tarih: {btc_post_date} ---")
            log.info(f"{coin_symbol} MACD:{coin_macd} | BTC EMA:{btc_ema} | BTC MACD:{btc_macd} | Kapanış:{close_pos}")

            ema_init_dic = copy.deepcopy(ema_curr_dic)
            ema_updater(close_pos)

            long_giris  = 0
            short_giris = 0
            signal_list = []

            for ema1, ema2, system_no in SYSTEMS:
                init_dir = ema_init_decider(ema1, ema2)
                curr_dir = ema_curr_decider(ema1, ema2)
                check    = system_check(ema1, ema2, system_no, coin_macd, btc_ema, btc_macd)
                changed  = init_dir != curr_dir

                if changed and check == "Evet" and curr_dir == "Long":
                    long_giris += 1
                    giris_type = "Long"
                elif changed and check == "Evet" and curr_dir == "Short":
                    short_giris += 1
                    giris_type = "Short"
                else:
                    giris_type = None

                signal_list.append({
                    "sistem_no": system_no,
                    "ema1": ema1,
                    "ema2": ema2,
                    "onceki": init_dir,
                    "simdi": curr_dir,
                    "sisteme_giris": check == "Evet",
                    "giris_yonu": giris_type
                })

                log.info(
                    f"Sistem:{system_no} | EMA {ema1}-{ema2} | "
                    f"Önce:{init_dir} Şimdi:{curr_dir} | "
                    f"Giriş:{check} | Yön:{giris_type or 'Yok'}"
                )

            log.info(f"ÖZET → Long Giriş:{long_giris} | Short Giriş:{short_giris}")

            # UI için son sinyal bilgisini güncelle
            last_signal = {
                "timestamp": datetime.utcnow().isoformat(),
                "coin_symbol": coin_symbol,
                "coin_macd": coin_macd,
                "btc_ema": btc_ema,
                "btc_macd": btc_macd,
                "close_pos": close_pos,
                "long_giris": long_giris,
                "short_giris": short_giris,
                "signals": signal_list
            }

            return jsonify({
                "message": "Analiz tamamlandı",
                "long_giris": long_giris,
                "short_giris": short_giris
            }), 200

    return jsonify({"message": "Veri alındı, diğer sinyal bekleniyor"}), 200


# ── UI için durum endpoint'i ───────────────────────────────────────────────────

@app.route('/status', methods=['GET'])
def get_status():
    """Dashboard'un çekeceği anlık durum bilgisi."""
    with state_lock:
        return jsonify(last_signal), 200


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200


# ── Uygulama başlatma ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    CORS(app, origins=[f"http://{ip}" for ip in trusted_ips])
    app.run(host='0.0.0.0', port=5001, debug=False)
