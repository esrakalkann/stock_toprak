from flask import Flask, request, jsonify
import ast
import copy

app = Flask(__name__)

coin_post_flag = 0
btc_post_flag = 0

btc_macd = None
btc_ema = None
coin_macd = None
close_pos = None
coin_symbol = None
btc_post_date = None

ema_curr_dic = {}
ema_init_dic = {}

for i in range(1, 501):
    ema_curr_dic[i] = 0

def ema_updater(price):
    for period in ema_curr_dic:
        k = 2 / (period + 1)
        ema_curr_dic[period] = price * k + ema_curr_dic[period] * (1 - k)

def ema_init_decider(a, b):
    return "Long" if ema_init_dic[a] > ema_init_dic[b] else "Short"

def ema_curr_decider(a, b):
    return "Long" if ema_curr_dic[a] > ema_curr_dic[b] else "Short"

def system_check(a, b, system_no, coin_macd, btc_ema, btc_macd):
    if system_no == 2:
        return "Evet" if coin_macd == btc_macd else "Hayır"
    if system_no == 5:
        return "Evet" if coin_macd == btc_ema else "Hayır"
    if system_no == 6:
        return "Evet" if btc_ema == btc_macd else "Hayır"
    return "Hayır"

def master_bias(long_count, short_count):
    total = long_count + short_count
    if total == 0:
        return "NO_TRADE", 0
    long_ratio = long_count / total
    short_ratio = short_count / total
    if long_ratio >= 0.70:
        return "STRONG_LONG", round(long_ratio * 100)
    elif long_ratio >= 0.55:
        return "LONG", round(long_ratio * 100)
    elif short_ratio >= 0.70:
        return "STRONG_SHORT", round(short_ratio * 100)
    elif short_ratio >= 0.55:
        return "SHORT", round(short_ratio * 100)
    else:
        return "NO_TRADE", round(max(long_ratio, short_ratio) * 100)

@app.route('/data', methods=['POST'])
def receive_data():
    global coin_post_flag, btc_post_flag, btc_ema, btc_macd, coin_macd, close_pos, coin_symbol, btc_post_date, ema_init_dic, ema_curr_dic

    data = request.data.decode('utf-8')
    long_giris = 0
    short_giris = 0

    if not data:
        return jsonify({"error": "no data"}), 400

    dict_data = ast.literal_eval(data)

    if dict_data.get("symbol") == "BTCUSD":
        btc_macd = "Short" if dict_data.get("macd_signal") == "0" else "Long"
        btc_ema = "Short" if dict_data.get("ema_signal") == "0" else "Long"
        btc_post_date = dict_data.get("time")
        btc_post_flag = 1
    else:
        coin_symbol = dict_data.get("symbol")
        coin_macd = "Short" if dict_data.get("macd_signal") == "0" else "Long"
        close_pos = float(dict_data.get("close"))
        coin_post_flag = 1

    if coin_post_flag == 1 and btc_post_flag == 1:
        coin_post_flag = 0
        btc_post_flag = 0

        ema_init_dic = copy.deepcopy(ema_curr_dic)
        ema_updater(close_pos)

        systems = [
            (25,50,2),(26,48,2),(26,49,6),(26,50,6),(27,47,2),(27,48,6),(27,49,5),(27,50,6),
            (28,46,5),(28,47,6),(28,48,5),(28,49,6),(28,50,2),(29,45,6),(29,46,6),(29,47,5),
            (29,48,6),(29,49,2),(30,44,6),(30,45,6),(30,46,5),(30,47,2),(30,48,6)
        ]

        for a, b, system_no in systems:
            if ema_init_decider(a,b) != ema_curr_decider(a,b) and system_check(a,b,system_no,coin_macd,btc_ema,btc_macd) == "Evet":
                if ema_curr_decider(a,b) == "Long":
                    long_giris += 1
                else:
                    short_giris += 1

        bias, confidence = master_bias(long_giris, short_giris)

        return jsonify({
            "symbol": coin_symbol,
            "long_systems": long_giris,
            "short_systems": short_giris,
            "bias": bias,
            "confidence": confidence,
            "btc_macd": btc_macd,
            "btc_ema": btc_ema,
            "coin_macd": coin_macd,
            "price": close_pos,
            "time": btc_post_date
        }), 200

    return jsonify({"status": "waiting"}), 200

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5001)




