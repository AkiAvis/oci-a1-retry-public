# OCI A1 bounded recovery

OCI Resource Manager の A1 Apply を、確認できた失敗分類に限定して次回の定期実行で再試行する仕組みです。実際の定期実行では OCI と GitHub に接続し、条件を満たす場合は Apply を開始します。

## 修正した安全境界

- `has_active_job` は `data` が配列で、各要素に非空の job ID と lifecycle state があることを検証します。不正な `data` を空一覧として扱いません。
- `small-01` / `small-02` の `provider_checksum_download_timeout` だけは狭い bounded recovery classifier で判定します。Oracle provider の公式 GitHub checksum URLは、行全体が正確な HTTPS host/path/artifact end の bare URL または `"URL":` 形式であることを必須にし、release tag version と `SHA256SUMS` filename version の一致も要求します。error block が `Error while installing oracle/oci v...` を明示する場合は、その provider version と URL version も一致させます。provider version を明示しない最小の legacy log では、URL 内の release/file version 一致だけを検証します。別 origin の埋め込み、query/fragment、port/userinfo、未知の後続文字列、artifact suffix、壊れた quoting を許可しません。Console の timestamp/INFO/ERROR 行・空行と、CLI の行折返しを認識し、繰り返された timeout block は個別に検証します。block 外の free-standing な timeout 文字列は根拠にしません。
  - `Failed to install provider`
  - `failed to retrieve authentication` → `checksums for provider`
  - `context deadline exceeded (Client.Timeout exceeded while awaiting headers)`
- small の別 provider、別 origin、plan/apply・mutation 後、容量不足や別の異常との混在、追加の未知 Error/ERROR/INFO record、署名不足、standalone/mixed unknown error は停止します。既知の Terraform progress である resource address 付き `Still creating... [Ns elapsed]` は容量不足 block 前の正常な継続行として扱いますが、任意の benign `WARN` や未知 notice は許可しません。core の同じ timeout は小さい側の再試行分類に入りません。
- **KNOWN UNSOLVED — core の既存 baseline 挙動を維持:** core の既存 failed history と freshly failed core job の容量判定は、baseline と同じ `Out of host capacity` marker match を使います。そのため、core のログに別の unknown error、provider 断片、timeout、権限・認証・quota・429、未知 INFO/WARN、構造化/不正形式の行が併記されても、容量 marker があれば core → small-01 fallback に進む可能性があります。これは pre-existing mixed-error safety limitation の記録であり、修正でも保証でもありません。`test_known_unsolved_core_baseline_bypass_*` で明示的にカバーし、core の全 unknown error が fail closed とは主張しません。
- core にも `history` の配列・entry・APPLY・state 一致、log 読み取り、active-job、inventory、重複、未知 target、429 defer などの独立した共有ガードは残します。従って baseline の容量 marker 挙動を保つことは、orchestrator 全体の挙動が baseline と同一という意味ではありません。
- history は `TIMECREATED DESC` の必要な範囲だけを厳格に検査します。small の最新から連続する timeout を OCI の既存履歴で数え、3件目で停止します。small の途中で正常な容量不足履歴に到達したらそこで境界を切り、境界より古い PLAN などは検査しません。未知 lifecycle、非 APPLY、取消、履歴/state/log の取得不良は fail closed です。未知の state は診断出力では `UNKNOWN` にクランプします。
- target label、state、safe job identifier だけを診断に出し、CLI の生エラーやログ本文は出しません。既存の inventory、active-job、重複、未知インスタンス、成功済みだが対象不在、429 defer の安全基準は維持します。
- 認識済み resource syntax の中にある Terraform payload は、timestamp wrapper の有無にかかわらず、属性・nested block・closing brace の構文だけを small の分類継続として扱います。属性値や resource 名が変わっても継続できますが、その内容が意味的に正常かを universal に判定するものではありません。実 OCI CLI のログ形式と、本番での動作も未確認です。

## オフライン TDD 検証

テストは `/usr/bin/bash`、`/usr/bin/jq`、Python 標準ライブラリを使用します。fake OCI CLI と fake inventory を一時ディレクトリに作成し、子 process の環境は allowlist で構成します。テストでは実 OCI/GitHub API、Terraform Apply、実リソース作成、認証情報の子 process への引き渡しはありません。

### RED → GREEN evidence

この作業で追加した再現テストは、修正前に次の不具合を RED で確認しました。

- 実形式に近い容量不足ログで core 再試行と small-01 fallback が止まる。
- 実ログをサニタイズした small-01 の Console 表形式と CLI 行折返し形式で、core → small-01 の順に進む。
- 容量不足境界より古い PLAN を全履歴検証して停止する。
- `data` が配列でない job list を空一覧として通過する。
- checksum timeout の別 origin / 別 provider を許可する。
- supplied downloaded small log を4 block全体のまま redaction して replay し、公式 URL の wrong-origin/query、port、credentials/userinfo、fragment、未知 trailing text、artifact suffix、malformed quoting、release/file version mismatch を small-02 の fake target なしで停止する。明示された provider version と URL version の mismatch も停止し、provider version がない legacy log と release/file version が一致する log は exact `stack-small-02` target に進む。bare URL と `"URL":` の実在形式は exact `stack-small-02` target に進む。
- small の容量不足ログに resource address 付き `Still creating... [10s elapsed]` を挿入した場合は、candidate と baseline の両方が exact `stack-small-02` target に進む。一方、任意の synthetic `WARN` は candidate で停止し、baseline との continuity 差を安全側の既知挙動として維持する。
- 実際の downloaded core + small-01 full logs に、仮想の harmless な新規 `WARN` prelude record を core failure の前に挿入すると、現 candidate は fake core 後に停止して small-01 に進まない。修正後は core → small-01 になり、同じ core path を baseline script でも replay しました。baseline の small provider-timeout 判定は既知の別問題を避けるため clean capacity small history に分離しています。
- core の容量 marker と別の provider/error/timeout/INFO/WARN を混在させた synthetic logs は、baseline-compatible な **KNOWN UNSOLVED** bypass として保持しています。これらは fail-closed の証拠ではありません。small の provider timeout と `InvalidParameter` / `AccountSuspended` / structured unknown の混在は従来どおり stop します。
- `Service has been deactivated`、`A condition has changed`、opaque な INFO row、bare standalone contamination を small Console log の failure 前の複数位置で受け入れないことを RED で確認しました。core 側の同じ synthetic rows は baseline limitation の明示的テストです。正常な provider 再初期化、Terraform plan/resource payload、core → small-01 の target sequence は維持します。
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

- core の capacity + unknown mixed-error bypass は **KNOWN UNSOLVED** のままです。これは baseline 互換のために意図的に残した意味上の例外で、release safety の解決ではありません。
- private downloaded/Console logs の offline replay は実 OCI CLI の live stdout、provider inventory、account-wide free-tier/billing 状態、次回 scheduler の実行結果を証明しません。
- これらの未解決 gate が満たされない限り、この candidate を production に deploy/activate しません。

## 変更範囲

変更したのは次の3ファイルだけです。

- `scripts/orchestrate-a1.sh`
- `tests/test_orchestrate_a1.py`
- `README.md`

この修正では workflow、`scripts/list-a1-instances.py`、Git 設定、Cloudflare の定期設定、認証情報を変更していません。オフラインのテスト結果は、実 OCI の稼働状態や無料枠の利用状況を保証するものではありません。
