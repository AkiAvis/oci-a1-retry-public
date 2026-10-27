# OCI A1 bounded recovery

OCI Resource Manager の A1 Apply を、確認できた失敗分類に限定して次回の定期実行で再試行する仕組みです。実際の定期実行では OCI と GitHub に接続し、条件を満たす場合は Apply を開始します。

## 修正した安全境界

- `has_active_job` は `data` が配列で、各要素に非空の job ID と lifecycle state があることを検証します。不正な `data` を空一覧として扱いません。
- 容量不足と provider timeout は error record/block 単位で判定します。容量不足は exact な `Out of host capacity` marker または認識済み block と、実際の Terraform 初期化・plan・resource 作成試行の INFO 継続だけを許可し、追加の未知 `Error:`、OCI `ERROR` level、provider 断片、タイムアウト、権限・認証・quota・429 の混在は停止します。
- `provider_checksum_download_timeout` は `small-01` / `small-02` の初期化時だけ許可します。Oracle provider の公式 GitHub checksum URLと、次の3署名を同じ provider-error block 内で順序どおり必須にします。Console の timestamp/INFO/ERROR 行・空行と、CLI の行折返しを認識し、繰り返された timeout block は個別に検証します。block 外の free-standing な timeout 文字列は根拠にしません。
  - `Failed to install provider`
  - `failed to retrieve authentication` → `checksums for provider`
  - `context deadline exceeded (Client.Timeout exceeded while awaiting headers)`
- 別 provider、別 origin、plan/apply・mutation 後、容量不足や別の異常との混在、追加の未知 Error/ERROR record、署名不足は停止します。core の同じ timeout は小さい側の再試行分類に入りません。
- history は `TIMECREATED DESC` の必要な範囲だけを厳格に検査します。最新から連続する timeout を OCI の既存履歴で数え、3件目で停止します。途中で正常な容量不足履歴に到達したらそこで境界を切り、境界より古い PLAN などは検査しません。未知 lifecycle、非 APPLY、取消、履歴/state/log の取得不良は fail closed です。未知の state は診断出力では `UNKNOWN` にクランプします。
- target label、state、safe job identifier だけを診断に出し、CLI の生エラーやログ本文は出しません。既存の inventory、active-job、重複、未知インスタンス、成功済みだが対象不在、429 defer の安全基準は維持します。
- 最初のエラー記録より前の、形式が定まらない INFO メッセージは完全には分類できません。確認済みの認証・アカウント異常は検出しますが、すべての未知の異常を検出できる保証はありません。実 OCI CLI のログ形式と、本番での動作も未確認です。

## オフライン TDD 検証

テストは `/usr/bin/bash`、`/usr/bin/jq`、Python 標準ライブラリを使用します。fake OCI CLI と fake inventory を一時ディレクトリに作成し、子 process の環境は allowlist で構成します。テストでは実 OCI/GitHub API、Terraform Apply、実リソース作成、認証情報の子 process への引き渡しはありません。

### RED → GREEN evidence

この作業で追加した再現テストは、修正前に次の不具合を RED で確認しました。

- 実形式に近い容量不足ログで core 再試行と small-01 fallback が止まる。
- 実ログをサニタイズした small-01 の Console 表形式と CLI 行折返し形式で、core → small-01 の順に進む。
- 容量不足境界より古い PLAN を全履歴検証して停止する。
- `data` が配列でない job list を空一覧として通過する。
- checksum timeout の別 origin / 別 provider を許可する。
- さらに、容量不足と provider エラー断片の混在、provider timeout と `InvalidParameter` / `AccountSuspended` の追加 Error、容量不足後の未知 OCI `ERROR` level も RED で確認しました。
- unknown lifecycle を active-job なしとして通過させず、未知 state を診断に漏らす。

修正後は上記の focused tests をすべて GREEN にし、実ログ形式をサニタイズしたメモリ内 fixture replay、関連する既存 guard と小さい側の連続 timeout 上限も含めて次を実行しました。

```bash
/usr/bin/bash -n scripts/orchestrate-a1.sh
/usr/bin/python3 -m py_compile tests/test_orchestrate_a1.py
/usr/bin/python3 -m unittest discover -s tests -p 'test_*.py' -q
# Ran 37 tests ... OK

git diff --check
```

## 変更範囲

変更したのは次の3ファイルだけです。

- `scripts/orchestrate-a1.sh`
- `tests/test_orchestrate_a1.py`
- `README.md`

この修正では workflow、`scripts/list-a1-instances.py`、Git 設定、Cloudflare の定期設定、認証情報を変更していません。オフラインのテスト結果は、実 OCI の稼働状態や無料枠の利用状況を保証するものではありません。
