# Connecting Real Quantum Hardware to the METASTATE L2 Router

This makes `/v1/quantum/route` execute on a **real IBM quantum computer** instead
of the local simulator. IBM's **Open Plan is free** (about 10 minutes of QPU time
per month, real hardware, no credit card).

The design: a small worker service (`metastate-quantum`) holds your IBM
credentials and talks to the QPU. Your Space calls the worker. Credentials never
touch the public Space.

```
  METASTATE Space  --/v1/quantum/route-->  metastate-quantum (Render)
                                                |
                                                v  Qiskit Runtime
                                          IBM Quantum QPU (real)
```

---

## STEP 1 — Create a free IBM Quantum account

1. Go to **https://quantum.cloud.ibm.com** and sign up (free).
2. On the dashboard **Home**, find your **API key** (a 44-character token).
   Copy it. This is `IBM_QUANTUM_TOKEN`.
3. You also need your **instance CRN** (Cloud Resource Name). On the platform,
   open your instance details; copy the CRN string (starts with `crn:v1:...`).
   This is `IBM_QUANTUM_CRN`.

   > If you only see an API key and no CRN, create a free instance first
   > (the platform prompts you to create an "Open" instance). The Open plan
   > gives free monthly runtime on real systems.

**What to send me / keep:** nothing needs to come to me. You will paste these two
values into Render in Step 3. Keep them private — the token is a secret.

---

## STEP 2 — Put the worker on GitHub

The worker lives in the `metastate-quantum/` folder I created.

1. Create a new GitHub repo, e.g. `metastate-quantum`.
2. Upload the four files: `app.py`, `requirements.txt`, `render.yaml`, `README.md`.

---

## STEP 3 — Deploy the worker on Render (free)

1. Go to **https://render.com**, sign up / log in (free).
2. **New → Web Service → Build and deploy from a Git repository**.
3. Connect the `metastate-quantum` repo. Render auto-detects `render.yaml`.
4. When prompted for environment variables, set three:

   | Key | Value |
   |-----|-------|
   | `IBM_QUANTUM_TOKEN` | your 44-char IBM API key (Step 1) |
   | `IBM_QUANTUM_CRN` | your instance CRN (Step 1) |
   | `WORKER_SHARED_SECRET` | invent a long random string (e.g. 32 random chars) |

5. Deploy. When it's live, open the service URL — `GET /` should return
   `{"service":"metastate-quantum","ibm_configured":true,...}`.
   Copy the service URL, e.g. `https://metastate-quantum.onrender.com`.

   > First boot is slow (Qiskit is large) and the free tier sleeps after
   > inactivity, so the first request after idle takes ~30–60s to wake. That's
   > fine — METASTATE has a 60s timeout and falls back to simulation if needed.

---

## STEP 4 — Point METASTATE at the worker

In your **HF Space → Settings → Variables and secrets**, add two secrets:

| Key | Value |
|-----|-------|
| `QUANTUM_WORKER_URL` | your Render URL, e.g. `https://metastate-quantum.onrender.com` |
| `QUANTUM_WORKER_SECRET` | the SAME `WORKER_SHARED_SECRET` you set on Render |

Restart the Space. Done.

---

## STEP 5 — Verify it's live

Call the Space endpoint:

```
curl -X POST https://cpater-metastate.hf.space/v1/quantum/route \
  -H "Content-Type: application/json" \
  -d '{"process_matrix": [[0.5,0.5],[0.3,0.7]], "backend": "auto"}'
```

- **Real hardware working** → response has `"hardware_status": "live (IBM Quantum)"`,
  a `"backend_used"` like `ibm_brisbane`, and a `"job_id"`.
- **Worker asleep / hardware busy** → `"hardware_status": "simulator (...)"` —
  it gracefully fell back, no error to the caller.

That's the whole loop. The L2 router is now genuinely routing process matrices to
a real quantum computer, with the simulator as a safety net.

---

## Notes & limits

- **Free plan caps qubits/time.** The worker caps circuits at 5 qubits and 4096
  shots to stay within the Open plan. Raise these only if you move to a paid plan.
- **Async reality.** Real QPU jobs queue. For small Open-plan circuits this is
  usually seconds-to-minutes; the worker waits for the result inline. If you later
  want very large jobs, we'd switch to a submit-now/poll-later pattern.
- **Rigetti / Braket** can be added the same way (extra worker routes + AWS creds)
  if you ever want them, but IBM Open is the free, no-billing starting point.
- **Security.** Only your Space (which knows `WORKER_SHARED_SECRET`) can trigger
  QPU jobs, so randoms can't burn your free minutes.
