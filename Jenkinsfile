// CI/CD for this agent-zero fork, hosted on a self-managed Jenkins (VPS).
// Requires a Multibranch Pipeline job pointed at this repo so
// `branch 'production'`-gated stages only run for that branch.
//
// Every push, any branch: checkout, install, lint, build (fully automatic,
// no approval needed). pytest is expected to be run locally before pushing,
// not in CI -- matching this repo's existing GitHub Actions setup, which
// also doesn't run pytest (see .github/workflows/docker-publish.yml).
//
// production branch only: an approval gate pauses the pipeline in the
// Jenkins UI before anything is pushed to Docker Hub -- nothing publishes
// without a human clicking Push within 15 minutes. Letting that window
// pass (or clicking Abort) skips the push instead of failing the build.
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
    timeout(time: 45, unit: 'MINUTES')
  }

  stages {
    stage('Diagnose DooD mount') {
      // TEMPORARY -- to be removed once the Docker-outside-of-Docker mount
      // path is confirmed. If Jenkins itself runs in a container, docker
      // run -v $WORKSPACE:... below asks the HOST daemon to bind-mount a
      // path that only exists inside the Jenkins container, not on the
      // host -- producing an empty/wrong mount. This inspects the Jenkins
      // container's own volume mounts to find the real host-side path.
      steps {
        sh '''
          echo "WORKSPACE=$WORKSPACE"
          echo "hostname=$(hostname)"
          [ -f /.dockerenv ] && echo "containerized: yes" || echo "containerized: no"
          docker inspect "$(hostname)" --format "{{json .Mounts}}" || true
        '''
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
        // .venv, created under the bind-mounted $WORKSPACE, is owned by
        // that user afterward -- not root -- and needs no special cleanup
        // before the next stage's container or the next build's checkout.
        sh '''
          set -euo pipefail
          docker run --rm \
            -u "$(id -u):$(id -g)" -e HOME=/tmp \
            -v "$WORKSPACE:/workspace" -w /workspace \
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
            -v "$WORKSPACE:/workspace" -w /workspace \
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
            -v "$WORKSPACE:/workspace" -w /workspace \
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
        // Pauses here until a human approves in the Jenkins UI -- lint
        // and build above already ran unattended; only the publish step
        // waits on a person. A single input() parameter returns its raw
        // value directly (not a map) -- must capture it explicitly or
        // RELEASE_VERSION would be empty in the next stage.
        //
        // Wrapped in its own timeout so silence has a safe default: no
        // approval within 15 minutes means "don't push", not "wait
        // forever" (the input step has no deadline of its own) and not
        // "fail the build" (the pipeline's own 45-minute timeout would
        // otherwise eventually abort the whole run). Catching the
        // interruption here and leaving RELEASE_VERSION unset lets the
        // next stage's `when` skip the push cleanly instead.
        script {
          try {
            timeout(time: 15, unit: 'MINUTES') {
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
            env.RELEASE_VERSION = null
            currentBuild.result = 'ABORTED'
            echo 'No approval within 15 minutes (or approval was declined) -- skipping the Docker Hub push.'
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
      post {
        always {
          sh 'docker logout || true'
        }
      }
    }
  }
}
