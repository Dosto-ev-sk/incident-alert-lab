# Windows incident-alert escalation lab

The browser talks to Flask on **127.0.0.1:5000**. SQLite stores synthetic alerts, UTC timestamps, and two possible call slots. A background worker checks for work every half second. In live mode it sends an authenticated HTTPS request to Twilio Programmable Voice with inline TwiML (`Say`, then `Hangup`). Twilio calls your configured mobile over the telephone network. No public webhook, tunnel, SMS, or push service is used. The Python code uses Twilio's REST API directly through `requests`.

## Provider eligibility: check before spending money

Official documentation reviewed **1 November 2026**:

- [Morocco Programmable Voice pricing](https://www.twilio.com/en-us/voice/pricing/ma) lists outbound mobile and Mobile Other at **US$1.0748/minute**, and local/Other at **US$0.5307/minute**. This is a published destination price, not confirmation that your account or particular +212 number is eligible. I could not retrieve a Morocco-specific official Voice guidelines page; do not infer unrestricted routing from the price table. Confirm your exact mobile prefix, caller ID and account eligibility with Twilio Support if the Console does not clearly permit them.
- The [current trial overview](https://www.twilio.com/docs/usage/trials) says trials last 30 days, calls are restricted to the signup country, and recipients must be verified (up to five). **Morocco is absent from its supported trial-country list. Do not assume a trial can call your Moroccan number.** Older Twilio help material describes broader trial reach; use current Console eligibility and ask Support about your specific account. An upgrade may be necessary, and does not itself guarantee that your route is available.
- [Voice trial documentation](https://www.twilio.com/docs/usage/trials/try-out-voice) permits custom TwiML with restrictions, including `Say` and `Hangup`. Trial calls use a Twilio trial number. This lab's speech is compatible with those verbs, but that does not remove geographic or account restrictions.
- [Voice Geographic Permissions](https://www.twilio.com/docs/sip-trunking/voice-dialing-geographic-permissions) apply to Programmable Voice as well as SIP. In the correct account/subaccount, open **Voice > Settings > Geo permissions**, find **Morocco**, enable the required permitted low-risk ranges, and save. Use **Phone Number Permission Check** for your exact number. Owner/Administrator access is required. Do not enable every country or high-risk ranges just to make a test work. A blocked or unavailable range may need Twilio Support.
- [Billing rounding](https://help.twilio.com/articles/223132307-How-do-you-round-minutes-for-billing-) rounds partial voice minutes upward. Two connected mobile calls each billed as one minute would total about **US$2.1496 in calling usage**, before taxes, caller-number rental or other charges, credits and account-specific rates. Recheck rates in your Console before testing. The [pricing API](https://www.twilio.com/docs/voice/pricing) supports destination-specific prices. The app requests a 45-second call time limit and 20-second ringing timeout; those are not a billing guarantee. Voicemail can answer and incur charges.

## Configure the Twilio account

1. Create/sign in to your own Twilio account and complete identity, email, phone and any required account/regulatory checks. Confirm eligibility for outbound Programmable Voice to your Moroccan mobile before upgrading or buying a number. If payment is required, add billing/funds in Twilio Console yourself.
2. Obtain an authorized **Voice-capable Twilio caller number**. On an eligible trial use the number assigned for that Voice recipient/product; after upgrade use a purchased Voice-capable number permitted for this route. A Moroccan caller number is not required by this application. Caller-ID routing rules still apply; ask Twilio if uncertain. Do not put an arbitrary number in `TWILIO_FROM_NUMBER`.
3. If your account requires verified recipients, open the Console's **Verified Caller IDs / verified numbers** area or Voice trial recipient setup, add your own destination and complete the code verification on your phone. Verification alone does not unlock a blocked country. Current upgraded accounts remove trial recipient restrictions, but geographic and routing restrictions remain.
4. Check Morocco Voice Geo Permissions and your exact number as described above. If unavailable or rejected, contact Twilio with your own-number synthetic alert use case and exact destination prefix. Do not try to bypass restrictions by changing signup country.
5. Find **Account SID** and **Auth Token** in your Twilio Console. Enter them in `.env` locally as below. Never paste them into chat, screenshots, source files, or issue reports. Use your account's real credentials only for your manual live test; automated tests substitute the HTTP client and never call Twilio.
6. Check Console Voice Logs and Debugger for account errors. The page stores HTTP status and a numeric Twilio error code, not the provider's response text. Errors such as 20003 (authentication), 21215 (geographic permission), 21219 (unverified recipient), or 21216 (blocked call) need provider/account investigation. Follow the actual error's official explanation in Console.

## PowerShell setup

Install Python 3.11+ for Windows if necessary (including the Python launcher). Open PowerShell in this project folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe lab.py
```

No activation script or execution-policy change is needed. If `py` is missing, use the full path to your installed `python.exe` for the first command. Open [the local lab](http://127.0.0.1:5000). The ordinary command **always uses dry-run**, even when `.env` says `DRY_RUN=false`. You can leave all credentials as placeholders for dry-run.

Click **Generate synthetic security alert**. The first slot becomes `simulated`. Wait at least 60 seconds without acknowledging to see exactly one simulated backup slot. Click **Acknowledge on computer**, then separately **Resolve alert**. Generate another alert and acknowledge it before the deadline to see escalation suppressed. One unresolved alert is permitted at a time; repeated Generate clicks return the existing alert.

## Enter local settings and manually test real calling

Edit the ignored `.env` with Notepad. Keep its variable names, replacing values locally:

```dotenv
DRY_RUN=false
TWILIO_ACCOUNT_SID=AC_REPLACE_LOCALLY
TWILIO_AUTH_TOKEN=REPLACE_LOCALLY
TWILIO_FROM_NUMBER=+REPLACE_WITH_AUTHORIZED_CALLER
ALERT_TO_NUMBER=+212_REPLACE_WITH_YOUR_MOBILE
ALERT_BACKUP_NUMBER=
```

The real destination must be E.164: for a Moroccan mobile starting with domestic `06` or `07`, remove the leading `0` and prepend `+212` (no spaces). The app validates `+2126` or `+2127` followed by eight digits; formatting validation does not confirm ownership or reachability. Existing process environment variables override `.env`; clear any stale Twilio/DRY_RUN variables locally if settings seem ignored. The program never prints their values.

Acknowledge and resolve dry-run alerts, then stop the app with Ctrl+C. Start real calling explicitly:

```powershell
.\.venv\Scripts\python.exe lab.py --live
```

Open the local page, check that it says **LIVE**, confirm that this is your own number using the checkbox, and click **Generate synthetic security alert**. That button initiates your live test, with up to two paid call attempts. Merely starting a fresh database does not create an alert. **Restarting with pending live alerts can resume their work**, as explained below.

The first call says:

> This is a cybersecurity lab test. High-severity alert on SRV-FILE01. Please acknowledge this alert on your computer.

If still unacknowledged after 60 seconds from alert creation, the second call begins **“Backup engineer escalation.”** and reads the same lab message. The first uses `ALERT_TO_NUMBER`; escalation uses `ALERT_BACKUP_NUMBER`, or the primary number if backup is empty. Answering or voicemail never acknowledges or resolves the alert. Acknowledgment happens only on the computer. Resolution requires prior acknowledgment and is a separate button.

`accepted` means Twilio accepted the API request and returned a Call SID. It **does not mean your phone rang, you answered, or speech played**. This basic version receives no status callbacks and performs no delivery polling. Check Voice Logs by the displayed Call SID for provider call status; even `completed` is not proof a human heard the message. `failed` means an explicit API rejection. `unknown` means acceptance could not be established. No failure or uncertain slot is retried; an unacknowledged alert can still use its single escalation slot.

## State, races, and restarts

- `lab.sqlite3` lives beside `lab.py`, independent of the terminal working directory. It stores UTC ISO timestamps, mode, a hash identifying the account/number configuration, alert acknowledgment/resolution, call reservations, request results and Call SIDs. It stores no auth token or full telephone numbers.
- The 60-second deadline starts when you generate the alert, not when the first call is answered. The worker dispatches at or after that deadline; this is a local lab, not a real-time scheduler. Keep Windows awake and its clock accurate. CPU stalls, sleep, network delays or a stopped process delay dispatch.
- SQLite `BEGIN IMMEDIATE` serializes acknowledgment and call reservation. If acknowledgment commits first, no new escalation is reserved. If the escalation reservation commits first, it may still be sent even if you immediately acknowledge. The reservation is the point of commitment; the webpage does not cancel an already committed/provider-queued call.
- The `(alert_id, slot)` primary key and slot check allow only slots 1 and 2. Failed, unknown and interrupted attempts consume their slots. Requests have no automatic POST retries. This provides **at most two API call attempts**, not exactly-once telephone delivery. Guaranteeing successful receipt or an exactly-once external action across a crash is impossible here.
- State survives a restart. With the same mode/account/numbers, unreserved initial work resumes and overdue unacknowledged escalation is dispatched once. Already reserved slots are **never replayed**, even if the app crashed before actually sending them. A `reserved` row left by a crash stays visible as an uncertain outcome; check Console. A remaining escalation can still run after its deadline. Acknowledged or resolved alerts do not generate more calls.
- Changing mode, account SID, caller, primary destination or backup destination pauses old alerts; they cannot silently turn from simulated into live calls or go to another number. You can still acknowledge/resolve them on the page. Token rotation does not pause them. Before restarting live when you want no pending calls, first run ordinary dry-run and acknowledge/resolve all pending alerts, stop it, then start live. Do not edit/delete call rows to retry them.
- The app binds only IPv4 loopback, checks Host and remote address, and protects POSTs with a random per-process form token. A restart invalidates old forms; refresh the page. It uses Waitress, no debugger/reloader, and no public endpoints. Run one instance via `lab.py`; binding the port happens before worker startup. Do not expose it with a proxy, tunnel or public binding.
- Keep `.env` private to your Windows account. `.gitignore` excludes credentials, SQLite files, environments and caches. Logs show only a destination's last two digits; errors omit raw exception/provider text. Do not enable HTTP wire/debug logging. The local UI includes Call SIDs for your own Console lookup.
- To reset a lab, stop it and preserve/rename `lab.sqlite3` yourself if desired. A reset creates a fresh call budget; never use it to replay an uncertain live call. No automated calling test has been performed as part of development.

## Project files and tests

All files are complete standalone files: `lab.py`, `templates/index.html`, `.env.example`, `.gitignore`, `requirements.txt`, `pytest.ini`, and `tests/test_lab.py`. No existing application files need editing. Tests cover deadline boundaries, two-slot limits, duplicate clicks, concurrent workers, both race outcomes, acknowledgment versus resolution, crash/restart, configuration isolation, inline TwiML, HTTP errors, timeouts, CSRF/Host protection, and live-button consent. The autouse fixture blocks the real requests transport; provider tests replace `post` with mocks. Importing the module never loads credentials, starts workers or makes calls.

## Two-number setup

Set `ALERT_TO_NUMBER` to your initial recipient and `ALERT_BACKUP_NUMBER` to your backup recipient. Leave backup empty to call the same phone twice. Both must be your own Moroccan mobiles; verify both with Twilio if your account requires verified recipients, and check geographic permission for each number. The UI and logs show only their last two digits.

## Zadarma account check

`zadarma_check.py` uses `ZADARMA_KEY` and `ZADARMA_SECRET` from the ignored local `.env`. Run `.\.venv\Scripts\python.exe zadarma_check.py` for read-only authentication, balance, destination-price, SIP and PBX checks. Its allowlist rejects call-origination endpoints. It does not switch the Flask lab to Zadarma. The live backend remains Twilio.

The connected Zadarma account was checked on 2 November 2026: authentication succeeded, balance was USD 0.50, and both destination-specific price lookups returned USD 0.75/minute. Its sole voice menu was disabled (`type=none`) with zero scenarios. No calls were originated and no remote account settings were changed. The prices and balance may change; rerun the checker for current values. Zadarma Standard advertises per-second billing, so this balance is not automatically enough or insufficient for a specific test duration.

Zadarma callback uses SIP/PBX endpoints rather than inline TwiML. A speech-capable endpoint and a Zadarma calling backend still need configuring before this lab can deliver its spoken messages through Zadarma. See the [callback documentation](https://zadarma.com/en/services/calls/callback/) and [voice-menu setup](https://zadarma.com/en/support/faq/pbx/how-to-turn-on-voice-menu/).
