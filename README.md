# OCI A1 bounded recovery

OCI Resource Manager の A1 Apply を、確認できた失敗分類に限定して次回の定期実行で再試行する仕組みです。実際の定期実行では OCI と GitHub に接続し、条件を満たす場合は Apply を開始します。

## 修正した安全境界

- `has_active_job` は `data` が配列で、各要素に非空の job ID と lifecycle state があることを検証します。不正な `data` を空一覧として扱いません。
- `small-01` / `small-02` の `provider_checksum_download_timeout` だけは狭い bounded recovery classifier で判定します。Oracle provider の公式 GitHub checksum URL は、行全体が正確な HTTPS host/path/artifact end の bare URL または `"URL":` 形式であることを必須にし、release tag version と `SHA256SUMS` filename version の一致も要求します。error block が `Error while installing oracle/oci v...` を明示する場合は、その provider version と URL version も一致させます。provider version を明示しない最小の legacy log では、URL 内の release/file version 一致だけを検証します。別 origin の埋め込み、query/fragment、port/userinfo、未知の後続文字列、artifact suffix、壊れた quoting を許可しません。Console の timestamp/INFO/ERROR 行・空行と、CLI の行折返しを認識し、繰り返された timeout block は個別に検証します。block 外の free-standing な timeout 文字列は根拠にしません。
  - `Failed to install provider`
  - `failed to retrieve authentication` → `checksums for provider`
  - `context deadline exceeded (Client.Timeout exceeded while awaiting headers)`
- small の別 provider、別 origin、plan/apply・mutation 後、容量不足や別の異常との混在、追加の未知 Error/ERROR/INFO record、署名不足、standalone/mixed unknown error は停止します。既知の Terraform progress である resource address 付き `Still creating... [Ns elapsed]` は容量不足 block 前の正常な継続行として扱いますが、任意の benign `WARN` や未知 notice は許可しません。core の同じ timeout は小さい側の再試行分類に入りません。
- **core の容量判定は marker を維持し、追加の bounded guard を適用:** 既存 failed history と freshly failed core の両方で、`Out of host capacity` がある場合でも、次の別シグナルを検出したら停止します。
  - 正規の `[timestamp] ERROR - ...` / timestamped `ERROR` record。ただし、正確な `Error: 500-InternalError, Out of host capacity.` は許可します。
  - 容量診断ではない明示的な `Error: ...`、unwrapped error record、structured JSON の明示的な `ERROR`。structured JSON は、`{` だけを根拠にせず、balanced な multiline object（nested object を含む）を追跡し、`"level": "ERROR"` または `"message": "Error: ..."` の key/value を認識します。raw 行と timestamped `[...] INFO -` / timestamped INFO wrapper の両方を対象にし、通常の Terraform resource brace や attribute value はこの判定だけで error にしません。
  - `INFO` の exact な `Credentials rejected`、`AccountSuspended` / `Account suspended`。大文字小文字と余分な whitespace は正規化しますが、任意の設定値や任意の INFO 文を error とみなしません。
  - history では core Apply の作成前に停止します。fresh failure では失敗した core Apply だけを確認し、small-01 fallback は開始しません。
  - safety parser は成功時に exit 0 と machine-readable token `serious` または `clean` を返します。`awk` の exit 1/2/126/127 など全ての nonzero、空 stdout、末尾改行を除いて余分な文字列・未知 token は parser failure として fail closed にします（シェルのコマンド置換は末尾改行を取り除きます）。history は `core_safety_parser_failed` で停止し、fresh failure は既存の non-capacity failure として small-01 fallback を開始しません。unexpected parser の stdout は診断へ採用せず、stderr も抑制します。
- legitimate な core capacity record とその diagnostics、供給された downloaded/Console full log、追加された unrecognized harmless `WARN` は引き続き core → small-01 の経路を許可します。core の判定を small の厳格な positive full-log grammar に置き換えていません。
- **KNOWN UNSOLVED — core の任意の unstructured INFO/WARN の意味:** `InvalidParameter` のような INFO、`Account is disabled` / `Access revoked`、未知 notice、payload 内の opaque INFO、arbitrary JSON without recognized `level`/`message` error keys、partial provider fragment、generic timeout、malformed/alternate rendering など、上記の bounded guard が認識しない文字列は、harmless prelude と serious failure を常に区別できません。容量 marker があれば core → small-01 に進む可能性が残ります。これは fail-closed の保証ではありません。別の active-job、history/state/log、inventory、duplicate-target、unknown-target、429 defer の共有ガードは独立して維持しています。
- history は `TIMECREATED DESC` の必要な範囲だけを厳格に検査します。small の最新から連続する timeout を OCI の既存履歴で数え、3件目で停止します。small の途中で正常な容量不足履歴に到達したらそこで境界を切り、境界より古い PLAN などは検査しません。未知 lifecycle、非 APPLY、取消、履歴/state/log の取得不良は fail closed です。未知の state は診断出力では `UNKNOWN` にクランプします。
- target label、state、safe job identifier だけを診断に出し、CLI の生エラーやログ本文は出しません。既存の inventory、active-job、重複、未知インスタンス、成功済みだが対象不在、429 defer の安全基準は維持します。
- `get-job-logs-content --output json --query data --raw-output` の実観測 CLI 3.95.1 形式 `YYYY/MM/DD HH:MM:SS[TERRAFORM_CONSOLE] [INFO|WARN|ERROR] message` は、source と既知 severity が完全一致する行だけを既存の timestamp/level 分類へ正規化します。未知 source/severity や不正 wrapper は正規化せず、小側の strict classifier では停止します。完全な core 208行・small 54行の実ログをこの renderer で変換した private replay と、従来の downloaded/Console replay を分けてテストします。production pinned CLI 3.89.1 が同じ形式を出すことは未確認です。
- **本番反映保留:** small 側のログ形式変換で使用する `awk` が、途中までの出力を返して異常終了しても、その終了状態が検査されません。独立した模擬テストでは、変換処理が未知のエラーを含むログを途中まで出力して終了コード1を返した場合でも、後続の small 作成に進みました。これは模擬故障であり、実際に本番で発生したという観測ではありません。変換の失敗を安全側で止める修正と検証が必要です。
- core の任意の unstructured INFO/WARN の意味は依然として **KNOWN UNSOLVED** です。正規化は形式互換性の修正であり、unknown INFO/WARN fail-open リスクを解消しません。

## オフライン TDD 検証

テストは `/usr/bin/bash`、`/usr/bin/jq`、Python 標準ライブラリを使用します。fake OCI CLI と fake inventory を一時ディレクトリに作成し、子 process の環境は allowlist で構成します。テストでは実 OCI/GitHub API、Terraform Apply、実リソース作成、認証情報の子 process への引き渡しはありません。

### RED → GREEN evidence

この作業で追加・更新した再現テストは、修正前に次の不具合を RED で確認しました。

- 実形式に近い容量不足ログで core 再試行と small-01 fallback が止まる。
- 実ログをサニタイズした small-01 の Console 表形式と CLI 行折返し形式で、core → small-01 の順に進む。
- 容量不足境界より古い PLAN を全履歴検証して停止する。
- `data` が配列でない job list を空一覧として通過する。
- checksum timeout の別 origin / 別 provider を許可する。
- supplied downloaded small log を4 block全体のまま redaction して replay し、公式 URL の wrong-origin/query、port、credentials/userinfo、fragment、未知 trailing text、artifact suffix、malformed quoting、release/file version mismatch を small-02 の fake target なしで停止する。明示された provider version と URL version の mismatch も停止し、provider version がない legacy log と release/file version が一致する log は exact `stack-small-02` target に進む。bare URL と `"URL":` の実在形式は exact `stack-small-02` target に進む。
- small の容量不足ログに resource address 付き `Still creating... [10s elapsed]` を挿入した場合は、candidate と baseline の両方が exact `stack-small-02` target に進む。一方、任意の synthetic `WARN` は candidate で停止し、baseline との continuity 差を安全側の既知挙動として維持する。
- 実際の downloaded core full log に explicit `ERROR` / `Error:` / structured `ERROR` と exact INFO auth marker を容量 block の前後へ挿入すると、candidate は history で core fake Apply を作らず、fresh failure では core fake Apply だけで停止します。同じ full log の unrecognized harmless `WARN`、供給された downloaded/Console full log の正常経路は core → small-01 を維持します。
- 実際の downloaded core full log に、`{\n  "level": "ERROR",\n  "message": "NotAuthorizedOrNotFound"\n}` と `{\n  "message": "Error: InvalidParameter"\n}` の multiline structured record（nested object と bracket/timestamped `INFO` wrapper を含む）を追加し、history/fresh の fake target sequence を検証します。先頭の `{` だけでは停止せず、認識した key/value が balanced object 内にある場合だけ mixed serious error として停止します。
- core safety parser を一時ディレクトリ内の fake `awk` に置き換え、exit 1/2/126/127 と stdout/stderr noise を history/fresh の両方で fail closed にし、exit 0 の empty/unknown/multiline output も exact `core_safety_parser_failed`（history）または exact non-capacity error（fresh）へ map することを確認しました。`clean` / `serious` の成功 token はそれぞれ既存の core → small-01 と停止経路を維持し、予期しない parser 出力は漏らしません。
- core の任意の unstructured INFO/WARN、opaque payload row、partial provider fragment、generic timeout、malformed rendering は **KNOWN UNSOLVED** のままテストに残しています。これらは fail-closed の証拠ではありません。small の provider timeout と `InvalidParameter` / `AccountSuspended` / structured unknown の混在は従来どおり stop します。
- `Service has been deactivated`、`A condition has changed`、opaque な INFO row、bare standalone contamination を small Console log の failure 前の複数位置で受け入れないことを RED で確認しました。core 側の同じ synthetic rows は residual ambiguity の明示的テストです。正常な provider 再初期化、Terraform plan/resource payload、core → small-01 の target sequence は維持します。
- unknown lifecycle を active-job なしとして通過させず、未知 state を診断に漏らす。

修正後は上記の focused tests を GREEN にし、受入れ時は private `OCI_A1_RETRY_LOG_DIR` を指定して supplied downloaded log と Console export を秘密値をメモリ内で redacted して replay します。downloaded log/Console export の内容は fixture としてリポジトリに追加していません。private log replay を省略した実行は受入れ証拠にしません。テスト件数は固定値として README に記録せず、実行時の `unittest` 出力で確認します。

```bash
/usr/bin/bash -n scripts/orchestrate-a1.sh
/usr/bin/python3 -m py_compile tests/test_orchestrate_a1.py
env -i HOME="$HOME" PATH=/usr/bin:/bin TMPDIR="$TMPDIR" PYTHONDONTWRITEBYTECODE=1 OCI_A1_RETRY_LOG_DIR="$PRIVATE_LOG_DIR" /usr/bin/python3 -m unittest discover -s tests -p 'test_*.py' -q

git diff --check
```

## Candidate status and unresolved gates

この作業は offline candidate の TDD だけです。workflow dispatch、OCI Apply、Cloudflare/GitHub/Oracle への接続、quota/grant/key の追加、15分 scheduler の変更、deploy は行いません。テストの GREEN は無請求・無事故・無プライバシー/セキュリティ影響・Oracle 側問題なしを保証しません。

- core の explicit serious mixed-error guard は、検証できた ERROR/Error/structured ERROR（multiline と wrapper を含む）と exact INFO auth marker の範囲だけを改善します。任意の unstructured INFO/WARN、認識キーのない arbitrary JSON、malformed/alternate rendering を全て安全とは宣言しません。容量 marker による fallback が続く可能性も残ります。
- private downloaded/Console logs の offline replay は実 OCI CLI の live stdout、provider inventory、account-wide free-tier/billing 状態、次回 scheduler の実行結果を証明しません。
- これらの未解決 gate が満たされない限り、この candidate を production に deploy/activate しません。

## 変更範囲

変更したのは次の3ファイルだけです。

- `scripts/orchestrate-a1.sh`
- `tests/test_orchestrate_a1.py`
- `README.md`

この修正では workflow、`scripts/list-a1-instances.py`、Git 設定、Cloudflare の定期設定、認証情報を変更していません。オフラインのテスト結果は、実 OCI の稼働状態や無料枠の利用状況を保証するものではありません。
