// CI/CD for this agent-zero fork, hosted on a self-managed Jenkins (VPS).
// Requires a Multibranch Pipeline job pointed at this repo so
// `branch 'production'`-gated stages only run for that branch.
//
// Every push, any branch: checkout, install, lint, build (fully automatic,
// no approval needed). pytest is expected to be run locally before pushing,
// not in CI -- matching this repo's existing GitHub Actions setup, which
// also doesn't run pytest (see .github/workflows/docker-publish.yml).
//
// production branch only: a 5-minute review window pauses the pipeline in
// the Jenkins UI before anything is pushed to Docker Hub. Clicking Push
// approves immediately; clicking Abort explicitly declines and skips the
// push. Letting the window lapse with no response auto-approves with the
// suggested version and proceeds to push -- silence is not a safe default
// here by design; only an explicit Abort skips the publish.
//
// Requires on the Jenkins agent:
//   - Docker CLI + buildx, with the Jenkins user able to reach the daemon
//     AND able to run containers as their own host UID (Install/Lint/Build
//     below run `docker run -u "$(id -u):$(id -g)"` against a plain
//     `python:3.12-slim` image via `sh`, not the Docker Pipeline plugin's
//     `agent { docker {...} }` -- that plugin isn't assumed to be
//     installed, so this only needs the same Docker CLI the final push
//     stage already requires, nothing extra).
//   - A "Username with password" credential named dockerhub-credentials
//     (Docker Hub username + a Personal Access Token, not your account
//     password) -- create this in Jenkins yourself; the pipeline only
//     references it by ID, never touches the raw values in code.
// See JENKINS_SETUP.md for the one-time setup this Jenkinsfile assumes.

pipeline {
  // Single agent for the whole pipeline: Install/Lint/Build/Suggest/Push
  // all run plain `sh` steps against the host's Docker CLI (some of them
  // additionally shelling out to `docker run` for an ephemeral Python
  // container) -- there's no per-stage Jenkins agent switch to reason
  // about, so the workspace is just the workspace throughout. Only the
  // approval stage overrides this with `agent none`, so a pending manual
  // approval never holds a Jenkins executor hostage (a well-known
  // input-step pitfall).
  agent any

  options {
    disableConcurrentBuilds()
    timestamps()
    // Whole-pipeline safety net, not the real per-stage budget (see the
    // Push to Docker Hub stage's own timeout for that) -- this just needs
    // to comfortably cover every stage's worst case added together: fast
    // Install/Lint/Build (~5 min), a full 5-minute approval wait, and the
    // DockerfileLocal build+push itself, which pulls agent0ai/agent-zero-base
    // plus heavy ML deps (torch, faiss, sentence-transformers,
    // unstructured[all-docs]) and has taken over 30 minutes on a slow
    // network day even when it ultimately succeeded. 45 minutes was too
    // tight for that combination and killed two in-progress, otherwise
    // fine, buildx runs mid-push with "context canceled" -- Jenkins'
    // timeout sends an interrupt to whatever step is running when the
    // clock runs out, which looks like a build failure but isn't one.
    timeout(time: 90, unit: 'MINUTES')
  }

  stages {
    stage('Resolve host workspace') {
      // If Jenkins itself runs in a container (true on this VPS), $WORKSPACE
      // is a path INSIDE the Jenkins container (e.g.
      // /var/jenkins_home/workspace/agent-zero_production). Install/Lint/
      // Build below talk to the HOST's Docker daemon over the shared
      // socket, so a `docker run -v "$WORKSPACE:/workspace"` there asks the
      // HOST to bind-mount that path -- which doesn't exist on the host,
      // so Docker silently creates an empty directory and mounts that
      // instead (confirmed the hard way: .venv creation "succeeded" into
      // that empty mount, then `pip install -r requirements.dev.txt`
      // failed with "No such file or directory" because the real checkout
      // was never there).
      //
      // Fix: resolve the Jenkins container's own bind mount for
      // /var/jenkins_home via `docker inspect` on itself, and rewrite
      // $WORKSPACE to the equivalent HOST-side path. If Jenkins is NOT
      // containerized (the other setup this pipeline supports -- see
      // JENKINS_SETUP.md), that mount lookup returns nothing and
      // $WORKSPACE is already a real host path, so it's used as-is.
      steps {
        script {
          env.HOST_WORKSPACE = sh(
            script: '''
              set -euo pipefail
              jenkins_home_host="$(docker inspect "$(hostname)" --format '{{range .Mounts}}{{if eq .Destination "/var/jenkins_home"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"
              if [ -z "$jenkins_home_host" ]; then
                printf '%s' "$WORKSPACE"
              else
                printf '%s' "${jenkins_home_host}${WORKSPACE#/var/jenkins_home}"
              fi
            ''',
            returnStdout: true
          ).trim()
          echo "Resolved host-side workspace: ${env.HOST_WORKSPACE}"
        }
      }
    }

    stage('Install') {
      steps {
        // Only requirements.dev.txt (ruff, pytest, pyinstrument) -- NOT
        // requirements.txt. Lint (ruff) and Build (compileall) below only
        // parse/byte-compile source, they never import or execute it, so
        // the app's runtime deps buy nothing here. requirements.txt also
        // pulls faiss-cpu, sentence-transformers, openai-whisper, and
        // unstructured[all-docs] -- multiple GB via torch alone -- which
        // would make every single push slow and network-fragile for no
        // benefit. pytest (which DOES need requirements.txt) is
        // deliberately not run in this pipeline -- see the file header.
        //
        // Runs Python inside a throwaway `python:3.12-slim` container
        // (this Jenkins has no Python tool installation, and no Docker
        // Pipeline plugin for a declarative `agent { docker {...} }`) as
        // the Jenkins user's own host UID (`-u "$(id -u):$(id -g)"`) so
        // .venv, created under the bind-mounted $HOST_WORKSPACE (see the
        // "Resolve host workspace" stage above), is owned by that user
        // afterward -- not root -- and needs no special cleanup before the
        // next stage's container or the next build's checkout.
        sh '''
          set -euo pipefail
          docker run --rm \
            -u "$(id -u):$(id -g)" -e HOME=/tmp \
            -v "$HOST_WORKSPACE:/workspace" -w /workspace \
            python:3.12-slim \
            bash -c "python -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -r requirements.dev.txt"
        '''
      }
    }

    stage('Lint') {
      steps {
        // Rule selection and the pre-existing-debt ignore list live in
        // ruff.toml, not here -- see that file for why.
        sh '''
          docker run --rm \
            -u "$(id -u):$(id -g)" \
            -v "$HOST_WORKSPACE:/workspace" -w /workspace \
            python:3.12-slim \
            .venv/bin/ruff check .
        '''
      }
    }

    stage('Build') {
      steps {
        // "Build" here means a fast correctness pass (byte-compile every
        // tracked source tree, catching syntax errors) -- NOT a Docker
        // image build. The real image (agent0ai/agent-zero-base bring-up +
        // full A0 install) is multiple GB and minutes long; running it on
        // every push on every branch would blow past a VPS's disk budget
        // fast. That heavier build only happens in "Push to Docker Hub"
        // below, gated to the production branch and a human approval.
        sh '''
          docker run --rm \
            -u "$(id -u):$(id -g)" \
            -v "$HOST_WORKSPACE:/workspace" -w /workspace \
            python:3.12-slim \
            .venv/bin/python -m compileall -q \
              agent.py initialize.py models.py preload.py prepare.py \
              run_tunnel.py run_ui.py update_reqs.py \
              agents api docker extensions helpers plugins prompts tests tools
        '''
      }
      post {
        // .venv is only needed within this build's Install/Lint/Build
        // trio; clearing it here keeps the workspace tidy before the
        // production-only stages below (which don't need it) run.
        always {
          sh 'rm -rf .venv || true'
        }
      }
    }

    stage('Suggest version') {
      when {
        branch 'production'
      }
      steps {
        script {
          env.SUGGESTED_VERSION = sh(
            script: 'bash scripts/push-agent-zero.sh --suggest-version',
            returnStdout: true
          ).trim()
        }
      }
    }

    stage('Approve Docker Hub push') {
      // No agent: this stage only waits on a human via input() and holds
      // no executor while it does.
      agent none
      when {
        branch 'production'
      }
      steps {
        // Pauses here for a human in the Jenkins UI -- lint and build above
        // already ran unattended; only the publish step waits on a person,
        // and only briefly. A single input() parameter returns its raw
        // value directly (not a map) -- must capture it explicitly or
        // RELEASE_VERSION would be empty in the next stage.
        //
        // Wrapped in its own timeout so silence has a defined outcome: no
        // response within 5 minutes auto-approves with the suggested
        // version and proceeds to push (the input step has no deadline of
        // its own, and the pipeline's own 90-minute timeout is a much
        // coarser backstop, not a substitute for this). An explicit click
        // on "Abort" is still honored as a real human decision and skips
        // the push -- only silence, not a rejection, is treated as
        // approval. Distinguishing the two relies on the timeout step's
        // interruption cause carrying "Timeout" in its description, which
        // is a stable, version-independent string across Jenkins releases
        // (unlike matching on the internal cause class name).
        //
        // getCauses() needs a one-time Jenkins admin approval the first
        // time this signature is used: Manage Jenkins > In-process Script
        // Approval > Approve the pending
        // "method ...FlowInterruptedException getCauses" entry. Until
        // approved, the catch block itself throws
        // RejectedAccessException and fails the whole build instead of
        // resolving timeout vs. abort -- see JENKINS_SETUP.md.
        script {
          try {
            timeout(time: 5, unit: 'MINUTES') {
              env.RELEASE_VERSION = input(
                message: "Push asiqurrahman/agent-zero to Docker Hub as :production + :${env.SUGGESTED_VERSION}?",
                ok: 'Push',
                parameters: [
                  string(
                    name: 'RELEASE_VERSION',
                    defaultValue: env.SUGGESTED_VERSION,
                    description: 'Version tag to push (vX.Y, e.g. v2.11). Leave as suggested unless you need a specific bump.'
                  )
                ]
              )
            }
          } catch (err) {
            def timedOut = err.getCauses()?.any { cause ->
              cause.getShortDescription()?.contains('Timeout')
            }
            if (timedOut) {
              env.RELEASE_VERSION = env.SUGGESTED_VERSION
              echo "No approval within 5 minutes -- auto-approving ${env.SUGGESTED_VERSION} and proceeding to push."
            } else {
              env.RELEASE_VERSION = null
              currentBuild.result = 'ABORTED'
              echo 'Push was explicitly declined -- skipping the Docker Hub push.'
            }
          }
        }
      }
    }

    stage('Push to Docker Hub') {
      agent any
      when {
        allOf {
          branch 'production'
          expression { return env.RELEASE_VERSION != null }
        }
      }
      steps {
        // Explicit ceiling for the actual heavy work, decoupled from the
        // pipeline-level timeout above (which also has to cover the
        // 5-minute approval wait before this stage even starts). 60
        // minutes leaves real headroom over the ~30+ minutes this has
        // taken on a slow network day, while still failing a genuinely
        // stuck build well before the pipeline-level 90-minute net would.
        timeout(time: 60, unit: 'MINUTES') {
          withCredentials([usernamePassword(
            credentialsId: 'dockerhub-credentials',
            usernameVariable: 'DOCKERHUB_USERNAME',
            passwordVariable: 'DOCKERHUB_TOKEN'
          )]) {
            sh '''
              set -euo pipefail
              echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USERNAME" --password-stdin
              CLI_VERSION="${RELEASE_VERSION}" bash scripts/push-agent-zero.sh
            '''
          }
        }
      }
      post {
        always {
          sh 'docker logout || true'
        }
      }
    }
  }
}
