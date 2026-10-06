# Black Friday PC Price Intelligence

2026年ブラックフライデー向けノートPCの価格・在庫・購入条件を時系列で観測する無料基盤。

目的:
- 15分間隔で公開Webページを観測
- 価格、在庫、クーポン、ポイント、納期の変化を記録
- 同一商品の変化イベントを時刻付きで保存
- 過去の公開価格観測と2026年の実測を分離
- 今買う / 待つ の判断に使える履歴を作る

観測時刻の定義:
保存する時刻は販売店内部の値段変更時刻ではなく、当システムがその価格を確認した時刻（observed_at）。
内部変更時刻を推測して断定しない。

無料運用:
GitHub Actions + Python標準ライブラリ中心。変化がない場合は不要なコミットを行わない。

主なデータ:
data/historical_2025.csv = 公開情報から確認できる過去観測
data/change_events.jsonl = 価格・在庫・条件の変化イベント
data/current_latest.json = 最新観測値
data/reports/latest.md = 最新レポート

対象:
Lenovo / ASUS / HP / MSI / Acerの日本公式を優先。
Amazon.co.jp、楽天市場の公式/正規販売店、ヨドバシ、ビック、ヤマダ等を補助ソースとして利用。

取得失敗:
Amazon等はbot対策や地域・ログイン条件により取得失敗することがある。
取得不能を売り切れと解釈せず fetch_status=error として扱う。
