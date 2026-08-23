"""株価の時系列から単回帰トレンドを計算する共通ユーティリティ（J-Quants・Alpaca共通で使用）"""


def linear_trend(closes):
    """
    終値の時系列（古い順）から単回帰直線を当てはめ、(1日あたりの傾き, 決定係数R²) を返す。
    傾き>0かつR²が高いほど「一貫して右肩上がり」とみなせる。
    """
    n = len(closes)
    if n < 2:
        return 0.0, 0.0
    xs = range(n)
    x_mean = (n - 1) / 2
    y_mean = sum(closes) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, closes))
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0:
        return 0.0, 0.0
    slope = num / den
    intercept = y_mean - slope * x_mean
    ss_tot = sum((y - y_mean) ** 2 for y in closes)
    if ss_tot == 0:
        return slope, 1.0
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, closes))
    r_squared = 1 - (ss_res / ss_tot)
    return slope, r_squared
