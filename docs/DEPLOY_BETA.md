# Free public beta — hosting runbook

How Thought → Speak runs as a free beta: what we chose and why, the exact launch steps, how to run it day to
day, and the risks. Total cost: **$0 a month.**

## The setup

```
 visitor ──HTTPS──▶ yourname.duckdns.org ──▶ Oracle Cloud "Always Free" Ubuntu server
                     (free address)            ├─ Caddy: HTTPS, free certificate, auto-renewed
                                               ├─ Thought → Speak (one uvicorn process, built-in coach)
                                               └─ disk: SQLite database + recordings, backed up nightly
```

- **Coaching:** the built-in coach by default (`LLM_PROVIDER=local`). Optional free AI coaching through Groq
  (step 9) — a free-tier key with no card on file can't run up a bill.
- **Speech-to-text:** the visitor's browser (Chrome / Edge work best; on Firefox, people type).
- **Beta guard-rails:** at most 200 accounts (`BETA_MAX_USERS`) and 30 analyzed takes per person per day.

### Why Oracle Cloud Always Free

The app keeps its database (SQLite) and recordings on disk, and runs its analysis workers inside the web
process. So it needs a host with a **disk that survives restarts** and a process that stays up. Checked in
October 2026:

| Option | Free? | Disk survives restarts? | Sleeps when idle? | Verdict |
|---|---|---|---|---|
| **Oracle Cloud Always Free** | Yes: 2 Arm CPUs + 12 GB RAM (or 1 GB AMD micro), 200 GB disk, 10 TB/month traffic | **Yes** | No | **Chosen** — runs the app unchanged |
| Render free web service | Yes, 750 h/month | **No** — wiped on restart, sleep or redeploy | After 15 min (~1 min to wake) | Accounts and history would vanish |
| Koyeb free instance | Yes, 512 MB, 0.1 CPU | **No** (no volumes on free) | After 1 h | Same problem, and very small |
| Hugging Face Spaces | Docker Spaces need a paid plan | No | Yes | Not free for this app |
| Fly.io | No free tier for new accounts (~$2–4/month smallest server) | Yes | Optional | The paid fallback |
| PythonAnywhere free | 100 CPU-seconds, 512 MB | Yes | — | Too small to analyze speech |

Sources: [Oracle Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm) ·
[Oracle Arm allowance halved in 2026](https://terminalbytes.com/oracle-cloud-free-tier-changes-2026) ·
[Render free tier](https://render.com/docs/free) · [Koyeb instances](https://www.koyeb.com/docs/reference/instances) ·
[Hugging Face Spaces](https://huggingface.co/docs/hub/en/spaces-overview) ·
[Fly.io pricing 2026](https://temps.sh/blog/fly-io-pricing-2026) ·
[PythonAnywhere free accounts](https://help.pythonanywhere.com/pages/FreeAccountsFeatures/)

## Launch — about 45 minutes

Steps marked **(you)** need your own accounts; everything else is scripted.

### 1. Accounts (you)

1. **Oracle Cloud** — sign up for the free account. Oracle asks for a card to verify you are a real person;
   Always Free resources aren't charged. Pick the **home region** carefully — it can't be changed later and the
   free server must live there. Choose the one closest to your testers (e.g. *US West (San Jose)*).
2. **DuckDNS** — sign in at duckdns.org (with GitHub or Google), create a subdomain such as `thoughttospeak`, and
   copy your **token** from the top of the page. Your address becomes `thoughttospeak.duckdns.org`.
3. Optional but wise: in Oracle, **Billing → Budgets**, create a budget of $1 with an email alert, so you hear
   about any charge immediately.

### 2. Create the server (you, in the Oracle console)

**Compute → Instances → Create instance**

- **Image:** Canonical Ubuntu 24.04.
- **Shape:** *Ampere* → `VM.Standard.A1.Flex` with **2 OCPUs and 12 GB** (marked "Always Free-eligible").
  If you see "Out of capacity", try another availability domain, try again later, or use the AMD
  `VM.Standard.E2.1.Micro` (1 GB). The setup script adjusts itself for the small one.
- **Networking:** create a new virtual cloud network with a **public subnet**, and keep "Assign a public IPv4
  address" on.
- **SSH keys:** "Generate a key pair for me" → **save the private key** (e.g. `~/.ssh/oracle_tts.key`).
- Create. Note the **public IP address** on the instance page.

### 3. Open the web ports (you)

**Networking → Virtual cloud networks → your network → Security Lists → Default Security List → Add Ingress
Rules**, twice:

| Source CIDR | IP protocol | Destination port |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

(The setup script opens the same ports in the server's own firewall.)

### 4. Point your address at the server

On duckdns.org, type the server's public IP next to your subdomain and press **update ip**. (The setup script also
keeps it updated if you pass your token.)

### 5. Put the code on GitHub

The server installs from the `main` branch of `github.com/Gayathri2016/thought-to-speak`. Commit and push the
current version first.

### 6. Install (copy and paste)

On your Mac:

```bash
chmod 600 ~/.ssh/oracle_tts.key
ssh -i ~/.ssh/oracle_tts.key ubuntu@<server-ip>
```

On the server:

```bash
curl -fsSL https://raw.githubusercontent.com/Gayathri2016/thought-to-speak/main/deploy/oracle/setup.sh -o setup.sh
sudo DOMAIN=thoughttospeak.duckdns.org DUCKDNS_TOKEN=<your-token> bash setup.sh
```

It takes about 5 minutes and ends with **"Done — your beta is live at https://…"**. If it says HTTPS isn't
answering yet, it tells you which of steps 3–4 to check; Caddy keeps retrying the certificate on its own.

### 7. Check it like a tester

On your phone and laptop:

- [ ] The site opens over https with no warning, and shows the **Free beta** badge.
- [ ] Sign up, record a take with the mic, get feedback, and retry.
- [ ] **Privacy** and **Send feedback** links work (footer).
- [ ] `https://<your-address>/docs` shows "Not Found" (API docs are hidden in production).

### 8. Settings worth filling in

```bash
sudo nano /etc/thought-to-speak/env      # e.g. CONTACT_EMAIL=you@example.com, BETA_MAX_USERS, DAILY_TAKE_LIMIT
sudo systemctl restart thought-to-speak
```

### 9. Optional: free AI coaching (Groq, Meta Llama)

The built-in coach fixes grammar by rules. For a rewrite that understands what you meant — and for accurate
transcripts of mic recordings — turn on Groq's free tier, which runs Meta's Llama 4 Scout and Whisper:

1. Sign up at console.groq.com and create an API key. Treat it like a password: never paste it in chat or commit it.
2. Optional but wise: in Groq's **Data Controls**, turn on **Zero Data Retention**.
3. On the server:

   ```bash
   sudo nano /etc/thought-to-speak/env
   #   GROQ_API_KEY=<your key>
   #   LLM_PROVIDER=auto
   #   TRANSCRIBE_PROVIDER=auto
   sudo systemctl restart thought-to-speak
   ```

The badge in the app changes to **AI coach**, and the Privacy page switches to the wording that says transcripts
and recordings go to Groq. Free-tier limits (roughly 1,000 requests and 500k tokens a day for Llama 4 Scout,
2,000 transcriptions a day) are protected by `AI_DAILY_TAKES` (300) and `TRANSCRIBE_DAILY` (500): past them, or
whenever Groq says "rate limited", the built-in coach and the browser transcript take over automatically. To turn
it off, empty `GROQ_API_KEY` and restart.

## Running it

| Task | Command (on the server unless noted) |
|---|---|
| Ship a new version | push to `main`, then `sudo bash /opt/thought-to-speak/app/deploy/oracle/update.sh` — backs up first, rolls back automatically if the new version doesn't start |
| See the app's logs | `journalctl -u thought-to-speak -f` |
| See web traffic / HTTPS logs | `sudo tail -f /var/log/caddy/access.log` · `journalctl -u caddy -n 50` |
| Restart | `sudo systemctl restart thought-to-speak` |
| Back up now | `sudo systemctl start tts-backup` (also runs nightly at 03:30 Pacific; 7 days kept in `/var/lib/thought-to-speak/backups`) |
| **Copy a backup to your Mac (weekly)** | on your Mac: `bash deploy/oracle/pull-backup.sh ubuntu@<server-ip> ~/.ssh/oracle_tts.key` |
| How many testers? | `sudo -u tts sqlite3 /var/lib/thought-to-speak/thought_to_speak.db 'select count(*) from users'` (install `sqlite3` with apt first) |
| Free disk | `df -h /` |

**Uptime alerts:** add a free monitor (e.g. UptimeRobot) for `https://<your-address>/api/health`, so you get an
email if the site goes down.

### Restore from a backup

```bash
sudo systemctl stop thought-to-speak
sudo -u tts cp /var/lib/thought-to-speak/backups/<date>/thought_to_speak.db /var/lib/thought-to-speak/thought_to_speak.db
sudo -u tts rm -f /var/lib/thought-to-speak/thought_to_speak.db-wal /var/lib/thought-to-speak/thought_to_speak.db-shm
sudo -u tts cp -a /var/lib/thought-to-speak/backups/<date>/audio/. /var/lib/thought-to-speak/audio/
sudo systemctl start thought-to-speak
```

On a brand-new server, run `setup.sh` first, then copy your backup folder up with `scp` and restore as above.

## Risks and limits — read before sharing the link

- **Oracle may reclaim idle free servers.** Its docs say an Always Free server can be reclaimed if, over 7 days,
  CPU, network (and on Arm, memory) all stay under 20%. A quiet beta can look idle. Protection: the **weekly
  off-site backup** above, and `setup.sh` + restore rebuilds the site in about 15 minutes. Upgrading the account
  to Pay As You Go keeps Always Free resources at $0 and many users report it avoids reclamation, but Oracle
  doesn't document that, so treat it as unconfirmed and keep the budget alert on.
- **Arm servers are sometimes "out of capacity"** in popular regions. The 1 GB AMD micro server works too (the
  script adds swap and uses one analysis worker).
- **One server, no redundancy.** Fine for a beta; expect occasional downtime during Oracle maintenance.
- **Speech-to-text depends on the browser.** Chrome and Edge give live transcripts; Safari varies; Firefox
  users type. There is no server-side Whisper on the free plan.
- **Free means limits:** 200 accounts and 30 takes per person per day by default. Raise them in the settings file
  once you see how the server copes (`htop` while testers are active).
- **Privacy promises are on `/privacy`.** It switches its wording automatically when Groq is on. If you add a
  different AI provider (OpenAI, Anthropic), update that page first — it names Groq.

## When to leave the free plan

Move when testers notice slowness, you pass ~500 active users, or you need guaranteed uptime. Cheapest next step
with the same architecture: a small Fly.io machine with a volume (about $2–4/month), or the same Oracle server
on Pay As You Go. Both keep the SQLite + disk design unchanged.
