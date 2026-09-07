import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";
import { store as chatsStore } from "/components/sidebar/chats/chats-store.js";
import * as notifications from "/components/notifications/notification-store.js";

const API_PATH = "/plugins/_source_control/source_control";

function contextId() {
  return chatsStore.getSelectedChatId();
}

async function call(action, extra = {}) {
  return await callJsonApi(API_PATH, { action, context_id: contextId(), ...extra });
}

// Turns a unified diff (as produced by `git diff`/`git diff --cached`) into
// aligned {left, right} rows for a VS Code-style side-by-side view. Kept as
// a plain, dependency-free parser rather than Ace's vendored `ext/diff`
// extension -- that extension's `createDiffView` option contract could not
// be verified without a live browser session (see AGENTS.md), and getting
// an unverified library API wrong is worse than a simpler parser we fully
// control and can test as pure functions.
export function parseUnifiedDiff(diffText) {
  if (!diffText) return [];
  const lines = diffText.split("\n");
  const rows = [];
  let i = 0;
  while (i < lines.length && !lines[i].startsWith("@@")) i++;

  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("@@")) {
      rows.push({ hunkHeader: line });
      i++;
      continue;
    }
    if (line.startsWith("\\")) {
      // "\ No newline at end of file" -- not a content line.
      i++;
      continue;
    }
    if (line.startsWith(" ")) {
      const text = line.slice(1);
      rows.push({ left: { text, type: "context" }, right: { text, type: "context" } });
      i++;
      continue;
    }
    if (line.startsWith("-")) {
      const removed = [];
      while (i < lines.length && lines[i].startsWith("-")) {
        removed.push(lines[i].slice(1));
        i++;
        // A "\ No newline at end of file" marker can sit right after the
        // last removed line, before the added block starts (e.g. a change
        // that adds a trailing newline) -- skip it in place so it doesn't
        // end the removed/added pairing early.
        while (i < lines.length && lines[i].startsWith("\\")) i++;
      }
      const added = [];
      while (i < lines.length && lines[i].startsWith("+")) {
        added.push(lines[i].slice(1));
        i++;
        while (i < lines.length && lines[i].startsWith("\\")) i++;
      }
      const max = Math.max(removed.length, added.length);
      for (let k = 0; k < max; k++) {
        rows.push({
          left: k < removed.length ? { text: removed[k], type: "removed" } : { text: "", type: "empty" },
          right: k < added.length ? { text: added[k], type: "added" } : { text: "", type: "empty" },
        });
      }
      continue;
    }
    if (line.startsWith("+")) {
      rows.push({ left: { text: "", type: "empty" }, right: { text: line.slice(1), type: "added" } });
      i++;
      continue;
    }
    // Unrecognized line (e.g. a stray blank at EOF) -- skip rather than misrender.
    i++;
  }
  return rows;
}

// Untracked files have no unified diff to parse (no prior version exists) --
// the API returns the whole file content instead; render it as an
// all-added right-only column, matching VS Code's own convention.
function wholeFileAsAddedRows(text) {
  if (!text) return [];
  return text.split("\n").map((line) => ({
    left: { text: "", type: "empty" },
    right: { text: line, type: "added" },
  }));
}

const model = {
  loading: false,
  isGitRepo: false,
  currentBranch: "",
  remoteUrl: "",
  lastCommit: null,
  staged: [],
  unstaged: [],
  untracked: [],
  error: "",
  selected: null, // { path, staged, untracked }
  diffText: "",
  diffLoading: false,
  commitMessage: "",
  committing: false,
  pushing: false,
  pulling: false,

  async onMount() {
    await this.refresh();
  },

  async onOpen() {
    await this.refresh();
  },

  cleanup() {
    this.selected = null;
    this.diffText = "";
  },

  hasChanges() {
    return this.staged.length > 0 || this.unstaged.length > 0 || this.untracked.length > 0;
  },

  async refresh() {
    this.loading = true;
    try {
      const response = await call("status");
      if (!response?.ok) {
        this.error = response?.error || "Failed to load source control status.";
        this.isGitRepo = false;
        this.staged = [];
        this.unstaged = [];
        this.untracked = [];
        return;
      }
      this.error = "";
      this.isGitRepo = Boolean(response.is_git_repo);
      if (!this.isGitRepo) {
        this.error = response.error || "";
        this.staged = [];
        this.unstaged = [];
        this.untracked = [];
        return;
      }
      this.currentBranch = response.current_branch || "";
      this.remoteUrl = response.remote_url || "";
      this.lastCommit = response.last_commit || null;
      this.staged = response.staged || [];
      this.unstaged = response.unstaged || [];
      this.untracked = response.untracked || [];

      // Keep the open diff in sync if its file is still listed; otherwise
      // close it rather than show a diff for a file that no longer applies.
      if (this.selected) {
        const stillPresent = this._findEntry(this.selected.path, this.selected.staged, this.selected.untracked);
        if (stillPresent) {
          await this.selectFile(stillPresent, { silent: true });
        } else {
          this.selected = null;
          this.diffText = "";
        }
      }
    } catch (error) {
      console.error("Error loading source control status:", error);
      this.error = String(error);
    } finally {
      this.loading = false;
    }
  },

  _findEntry(path, staged, untracked) {
    if (untracked) return this.untracked.includes(path) ? { path, staged: false, untracked: true } : null;
    const list = staged ? this.staged : this.unstaged;
    return list.find((entry) => entry.path === path) ? { path, staged, untracked: false } : null;
  },

  async selectFile(entry, { silent = false } = {}) {
    this.selected = entry;
    this.diffLoading = !silent;
    try {
      const response = await call("diff", {
        path: entry.path,
        staged: Boolean(entry.staged),
        untracked: Boolean(entry.untracked),
      });
      if (!response?.ok) {
        this.diffText = "";
        if (!silent) {
          notifications.toastFrontendError(response?.error || "Failed to load diff", "Source Control", 5, "source_control");
        }
        return;
      }
      this.diffText = response.diff || "";
    } catch (error) {
      console.error("Error loading file diff:", error);
      this.diffText = "";
    } finally {
      this.diffLoading = false;
    }
  },

  diffRows() {
    if (!this.diffText) return [];
    return this.selected?.untracked ? wholeFileAsAddedRows(this.diffText) : parseUnifiedDiff(this.diffText);
  },

  isSelected(path, staged, untracked) {
    return Boolean(
      this.selected &&
        this.selected.path === path &&
        Boolean(this.selected.staged) === Boolean(staged) &&
        Boolean(this.selected.untracked) === Boolean(untracked),
    );
  },

  async stage(path) {
    const response = await call("stage", { path });
    if (!response?.ok) {
      notifications.toastFrontendError(response?.error || "Failed to stage file", "Source Control", 5, "source_control");
      return;
    }
    await this.refresh();
  },

  async unstage(path) {
    const response = await call("unstage", { path });
    if (!response?.ok) {
      notifications.toastFrontendError(response?.error || "Failed to unstage file", "Source Control", 5, "source_control");
      return;
    }
    await this.refresh();
  },

  async stageAllUnstaged() {
    const paths = [...this.unstaged.map((entry) => entry.path), ...this.untracked];
    if (!paths.length) return;
    const response = await call("stage", { paths });
    if (!response?.ok) {
      notifications.toastFrontendError(response?.error || "Failed to stage changes", "Source Control", 5, "source_control");
      return;
    }
    await this.refresh();
  },

  async unstageAllStaged() {
    const paths = this.staged.map((entry) => entry.path);
    if (!paths.length) return;
    const response = await call("unstage", { paths });
    if (!response?.ok) {
      notifications.toastFrontendError(response?.error || "Failed to unstage changes", "Source Control", 5, "source_control");
      return;
    }
    await this.refresh();
  },

  async commit() {
    const message = this.commitMessage.trim();
    if (!message) {
      notifications.toastFrontendWarning("Enter a commit message first.", "Source Control", 4, "source_control");
      return;
    }
    this.committing = true;
    try {
      const response = await call("commit", { message });
      if (!response?.ok) {
        notifications.toastFrontendError(response?.error || "Commit failed", "Source Control", 5, "source_control");
        return;
      }
      this.commitMessage = "";
      notifications.toastFrontendSuccess("Committed.", "Source Control", 3, "source_control");
      await this.refresh();
    } finally {
      this.committing = false;
    }
  },

  async push() {
    this.pushing = true;
    try {
      const response = await call("push");
      if (!response?.ok) {
        notifications.toastFrontendError(response?.error || "Push failed", "Source Control", 6, "source_control");
        return;
      }
      notifications.toastFrontendSuccess(response.message || "Pushed.", "Source Control", 3, "source_control");
      await this.refresh();
    } finally {
      this.pushing = false;
    }
  },

  async pull() {
    this.pulling = true;
    try {
      const response = await call("pull");
      if (!response?.ok) {
        const detail = response?.conflicting_files?.length
          ? ` (conflicts: ${response.conflicting_files.join(", ")})`
          : "";
        notifications.toastFrontendError((response?.error || "Pull failed") + detail, "Source Control", 8, "source_control");
        return;
      }
      notifications.toastFrontendSuccess(response.message || "Pulled.", "Source Control", 3, "source_control");
      await this.refresh();
    } finally {
      this.pulling = false;
    }
  },
};

export const store = createStore("sourceControl", model);
