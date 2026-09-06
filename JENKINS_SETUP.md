# Jenkins CI/CD setup

This repo's `Jenkinsfile` assumes a Jenkins instance already running on your VPS, configured as follows.

## 1. Jenkins agent prerequisites

The machine running the Jenkins agent (built-in node is fine for a single VPS) needs:

- **Docker CLI + buildx**, with the Jenkins user able to reach the Docker daemon (either Jenkins runs directly on the VPS with its user in the `docker` group, or Jenkins itself runs in a container with the host's Docker socket mounted in). Every stage in the pipeline needs this -- Install/Lint/Build run Python inside a throwaway `python:3.12-slim` container via plain `sh 'docker run ...'` (not a Jenkins "Python tool" installation -- there isn't a first-party equivalent of the NodeJS tool plugin for Python -- and not the Docker Pipeline plugin's declarative `agent { docker {...} }`, which this Jenkins doesn't have installed), and the final push obviously needs it too.
  - If Jenkins itself runs in a container (as it does on jenkins.cloud.braintechsolution.com), `docker run -v $WORKSPACE:...` from inside that container would ask the HOST daemon to bind-mount a path that only exists inside the Jenkins container -- producing a silently empty mount. The pipeline's "Resolve host workspace" stage handles this automatically by inspecting the Jenkins container's own `/var/jenkins_home` bind mount and rewriting the path; nothing to configure for this.
- Enough free disk for the Docker Hub push build: the image built from `DockerfileLocal` is large (~11GB on disk from `agent0ai/agent-zero-base`). This only runs on the gated `production` push, not on every branch push -- the per-push Install/Lint/Build stages stay light (pip install + ruff + `compileall`, no image build) specifically to keep routine CI cheap.

## 2. Jenkins plugins

- **Pipeline** (ships by default with most Jenkins installs)
- **Git** / **GitHub Branch Source** -- needed for the Multibranch Pipeline job type below
- **Credentials Binding** -- for `withCredentials`, used in the push stage
- No **Docker Pipeline** plugin needed -- deliberately avoided since it wasn't already installed; Install/Lint/Build call `docker run` directly instead of using a declarative `agent { docker {...} }` block.

## 3. Docker Hub credential

Create a Jenkins credential yourself (never share the raw value with an assistant or commit it):

1. Jenkins -> **Manage Jenkins** -> **Credentials** -> (a suitable store/domain) -> **Add Credentials**
2. Kind: **Username with password**
3. Username: your Docker Hub username (`asiqurrahman`)
4. Password: a **Docker Hub Personal Access Token** (Docker Hub -> Account Settings -> Security -> New Access Token) -- not your account password
5. ID: `dockerhub-credentials` (the `Jenkinsfile` references this exact ID)

## 4. Job: Multibranch Pipeline

The `Jenkinsfile`'s `when { branch 'production' }` conditions require a **Multibranch Pipeline** job (not a plain Pipeline job):

1. Jenkins -> **New Item** -> **Multibranch Pipeline**
2. Branch source: Git (or GitHub), pointed at `https://github.com/asiqur-rahman/agent-zero-contrib.git`
3. **Add a branch filter so only `production` is discovered/built** -- under that branch source, click **Add** (behavior) -> **Filter by name (with regular expression)** -> Regular expression: `^production$`. Without this, Jenkins discovers and creates a sub-job for every branch in the repo (including short-lived feature/fix branches, especially since merged branches are kept around rather than deleted -- see the repo's own branch-cleanup convention), each triggering its own build on push. The `Jenkinsfile`'s per-stage `when { branch 'production' }` guards are a defense-in-depth safety net for a build that slips through anyway (it completes near-instantly with everything skipped), not a substitute for this filter -- this filter is what actually stops the noise.
4. Build configuration: **by Jenkinsfile**, path `Jenkinsfile` (default)
5. Save -- Jenkins scans branches and creates a sub-job for the branches matching the filter above (just `production`)

If other branches already have sub-jobs from before this filter was added, re-run **Scan Multibranch Pipeline Now** after saving -- Jenkins prunes sub-jobs for branches the filter now excludes.

## 5. Approve the getCauses() script signature

The "Approve Docker Hub push" stage distinguishes a lapsed timeout (auto-approve) from an explicit Abort click (skip the push) by inspecting `err.getCauses()` on the caught interruption. Jenkins' Groovy sandbox blocks this method by default the first time any Jenkinsfile calls it -- until approved, the `catch` block itself throws `RejectedAccessException` and fails the whole build instead of resolving timeout vs. abort (confirmed the hard way: this is exactly what failed build #13 the first time the 5-minute window lapsed).

One-time fix, needed on any fresh Jenkins install this Jenkinsfile runs on:

1. Let (or force) a build reach the "Approve Docker Hub push" stage and hit the RejectedAccessException once -- this is what registers the pending signature.
2. Jenkins -> **Manage Jenkins** -> **In-process Script Approval**
3. Find `method org.jenkinsci.plugins.workflow.steps.FlowInterruptedException getCauses` and click **Approve**
4. Re-run (or Replay) the failed build -- it will now resolve timeout vs. abort correctly

## 6. Create the `production` branch

This fork's upstream-tracking branches (`main`, `development`, `testing`, `ready`) mirror `agent0ai/agent-zero` and get synced from `upstream` -- they are not a good release-trigger, since a sync from upstream would fire the gate too. `production` is a dedicated branch that only exists on your fork, pushed to only when you actually want to cut a release:

```bash
git checkout -b production main
git push -u origin production
```

After the first push, re-run **Scan Multibranch Pipeline Now** on the job (or wait for its next scheduled scan) so Jenkins picks up the new branch and creates its sub-job.

## 7. Trigger on push

Both are configured, so a push is picked up near-instantly and the periodic scan is just a safety net if a delivery is ever missed:

- **Webhook (primary, near-instant):** GitHub repo -> Settings -> Webhooks, Payload URL `https://jenkins.cloud.braintechsolution.com/github-webhook/`, content type `application/json`, event: **Just the push event**. Created via `gh api repos/asiqur-rahman/agent-zero-contrib/hooks` (webhook id `674457553`) and verified with a ping delivery (`200 OK`) -- Jenkins is publicly reachable at this domain, so no tunnel was needed.
- **Polling (fallback):** the Multibranch Pipeline job's **Scan Multibranch Pipeline Triggers** -> "Periodically if not otherwise run" stays enabled underneath the webhook, so a build still eventually happens even if a webhook delivery is ever dropped.

## What the pipeline actually does

Everything below only runs on a push to `production` -- every stage (including Install/Lint/Build) is gated with `when { branch 'production' }`, and the Multibranch Pipeline job's own branch filter (step 3 above) should already stop other branches from being discovered/triggered in the first place. A build on any other branch (if one somehow still runs) completes near-instantly with everything skipped.

1. **Install**: creates a `.venv` in the workspace, installs `requirements.dev.txt` (which now includes `ruff`) -- not `requirements.txt`. Lint and Build below only parse/byte-compile source, never import or execute it, so the app's runtime deps (faiss-cpu, torch-via-sentence-transformers/whisper, unstructured[all-docs], ...) buy nothing here.
2. **Lint**: `ruff check .`, scoped by `ruff.toml` to syntax errors + pyflakes, with the codebase's pre-existing pyflakes debt explicitly carved out (see that file's comments) so this starts green without a repo-wide reformat.
3. **Build**: `python -m compileall` across the tracked source trees -- a fast correctness pass, not a Docker image build.

`pytest` is expected to be run locally before pushing to `production`, not in CI -- matching this repo's existing GitHub Actions setup, which also doesn't run it.

Then, still only on `production`:

1. Suggests the next version (reads Docker Hub's existing `vX.Y` tags on `asiqurrahman/agent-zero`, bumps the minor -- same logic as `make push`, matching upstream agent-zero's own major.minor tagging with no patch component)
2. **Opens a 5-minute review window in the Jenkins UI**, showing the suggested version (editable) before anything happens
3. Clicking **Push** within that window approves immediately with the (possibly edited) version. Clicking **Abort** explicitly declines and skips the push -- the build ends as `ABORTED`, and it does not push. Letting the window lapse with no response is treated as approval: it auto-proceeds with the suggested version, exactly as if Push had been clicked -- this is an intentional design choice (see the Jenkinsfile's own comments), not a safe-by-default timeout
4. Builds fresh via `DockerfileLocal` (the exact commit Jenkins checked out, not a separate `git clone` of a branch) and pushes `asiqurrahman/agent-zero:production` + `:vX.Y` to Docker Hub

## Local equivalents

- `make up` / `make down` / `make clean` / `make logs` -- build-and-run this checkout locally via `docker-compose.local.yml`.
- `make dev` -- run the WebUI natively (`python run_ui.py`), matching this repo's documented dev workflow (no hot-reload container).
- `make push` / `make push-check` -- the same `scripts/push-agent-zero.sh` the Jenkinsfile calls, runnable by hand from a console (prompts for the version instead of requiring `VERSION=`/`CLI_VERSION=`).

## Honesty check

This `Jenkinsfile` was written and carefully reviewed for correctness (agent placement, credential binding, the `input()` step's return-value quirk with a single parameter, why the executor-blocking pitfall needed fixing, and why per-stage Docker containers need an explicit `.venv` cleanup to avoid root-owned workspace files), but it has **not been run against a real Jenkins instance**. First real run should be watched, not assumed to work.
