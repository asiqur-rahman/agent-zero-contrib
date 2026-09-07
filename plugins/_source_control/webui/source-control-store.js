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
};

export const store = createStore("sourceControl", model);
