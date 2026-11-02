# Optional GitHub connection

FusionEdges keeps its original interface and two repository-link fields. Login is optional. Selecting a repository in the left sidebar fills “Your site”; the donor link and existing transfer/report/analysis controls stay available.

## One-time developer registration

Run `python3 -m fusionedges.github_setup` and open **http://127.0.0.1:8110/github-setup**. Click **Register FusionEdges on GitHub**, review the prefilled settings and create the app. The callback configures login automatically and saves only the needed app credentials in ignored `.github-workspaces/app-config.json` with owner-only file access. No secret needs copying or pasting. Then install on selected repositories using the setup page link and restart the main app once. Future users only use **Connect GitHub**.

The helper uses port 8110 to avoid interrupting the existing application; the login callback remains port 8101. Keep the helper running until GitHub returns to it. The manifest follows [GitHub's registration flow](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest).

## Start

Start the main app from the Desktop project in your terminal with your existing AI environment:

```bash
python3 -m fusionedges.server --port 8000
```

The main app also starts the optional connection service on port 8101. `zsh start-github-feature.command` is available if you need to start that service separately.

Open **http://127.0.0.1:8000**. Click **Connect GitHub** to open GitHub directly. Sign in and, if repository access has not been granted, select repositories on GitHub. The sidebar refreshes automatically while you complete access; the two link fields remain available. You can hide the list and keep pasting links. The connection service uses port 8101 so repository previews cannot access its authenticated APIs. Restart any older server still displaying the replaced workspace before starting these commands.

The original transfer flow is unchanged; selecting a private repository does not add authenticated cloning to that legacy flow. The separately implemented PR review workspace remains available at `http://127.0.0.1:8101/projects` as an optional secondary screen. Its transfer and publication flow is described below; it is not the default app or a required login step. Bringing its private-repository transfer and approval controls into the original result view remains follow-up work.

## User flow

1. Connect GitHub with a fine-grained token, or use GitHub App sign-in after one-time setup.
2. In the **left sidebar**, select/add the repository that should receive changes. Targets must be writable; public and private targets are supported.
3. Enter another person’s **public GitHub repository** and name a component. Optional instructions specify placement, props and host behavior to preserve.
4. Confirm trust before running repository build scripts on this Mac. Private targets require separate consent before code is sent to the configured AI provider.
5. The app pins source/donor commits, prepares the transfer in an account-owned local workspace, and records compatibility guidance, checks, screenshots and code diffs. No remote branch is created yet.
6. Inspect the proposed diff, verification results and donor attribution. Missing checks remain unverified. Failed/unknown checks require explicit acknowledgement before a draft can be opened.
7. **Approve & open draft PR** creates a `fusionedges/<component>-<id>` branch and a draft PR into the target’s default branch, normally `main`.
8. Review CI and merge on GitHub when ready. FusionEdges never force-pushes, updates the default branch directly, or automatically merges.

A new approval is required if local patch/check/report content changes. If the target default branch advanced since the integration started, regenerate against the latest commit. Partial publication can be retried; the app reuses its branch/PR rather than blindly pushing over an existing branch.

## Option A: fine-grained token for the local demo

In GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens:

- Give the token an expiry.
- Select only the target repositories you intend to change.
- Repository permissions: **Contents: Read and write**, **Pull requests: Read and write**, and required metadata read access.
- Enter it in the local app’s password field under “Connect with a fine-grained GitHub token.” Do not put it in chat, URLs, a repository, or a command saved in shell history.

Tokens are kept only in this local server’s memory. Session cookies are opaque IDs with HttpOnly/SameSite attributes. Browser localStorage, generated projects, worker input files and Git clone URLs do not contain the GitHub token. Disconnecting clears the current local connection; revoke a token in GitHub to revoke its GitHub access. An already running trusted integration may continue its local work after disconnection; publication still requires an authenticated connection.

Public donor repositories are read anonymously so they do not need to be included in a fine-grained token’s selected target repositories. Anonymous API rate limits can still apply. Private donors are not supported in this first version.

## Option B: “Continue with GitHub” using a GitHub App

Register a GitHub App with:

- Homepage: `http://127.0.0.1:8101/projects`
- User authorization callback: `http://127.0.0.1:8101/auth/github/callback`
- Repository **Contents** and **Pull requests** read/write permissions, plus required metadata access.
- User authorization enabled. Install it on selected target repositories.
- No webhook or private key is required for this user-access-token flow.

Configure these server environment variables locally:

```text
FUSIONEDGES_GITHUB_CLIENT_ID
FUSIONEDGES_GITHUB_CLIENT_SECRET
FUSIONEDGES_GITHUB_APP_SLUG
```

Restart only the feature server after configuration. Never paste the client secret into chat or commit it. The callback uses one-time state bound to the browser session and PKCE S256. Reconnect when the local session or GitHub token expires; refresh tokens are not persisted in this prototype.

GitHub App user tokens are limited to resources accessible to **both** the user and app installation. The repository selector reads that accessible installation set. See [GitHub’s user-token authorization documentation](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app) and [installation repository endpoints](https://docs.github.com/en/rest/apps/installations).

Draft creation uses GitHub’s [pull request API](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request). Draft availability and organization policies can affect the API response; this feature never silently falls back to publishing a ready-for-review PR.

## Local architecture and limits

This is a **local development prototype**, not a hosted multi-user deployment. GitHub sessions exist only in memory. Project metadata, private source archives, logs and generated results are stored under the feature worktree’s ignored `.github-workspaces/<GitHub-user-id>/…` folders; there is no public link to those files. Product endpoints enforce account ownership. They are not placed in the legacy lab’s public output database or files route.

A separate subprocess runs each integration with its own `FUSIONEDGES_HOME`, output database and tools folder. GitHub credentials never enter that worker. Build subprocesses have provider/token/secret environment variables removed, but this is **not an operating-system sandbox**. Trusted repository scripts still run as the local user. Hosted deployment would require isolated execution, durable encrypted credentials/session storage, HTTPS and a complete production security review. Legacy experiment execution/output routes are deliberately disabled on this authenticated product server.

The product currently uses rules → residual compatibility report → targeted AI repairs. It does not reuse the pilot’s output/progress or turn on historical memory RAG. No automatic result should be represented as independently visually reviewed. Source baseline checks are used when available; unsupported Next SSR preview paths remain unverified. Local browser screenshots do not become public GitHub attachments.

Publication carries donor source/commit/license attribution in the diff and PR description. License metadata is informational, not a determination that reuse is permitted. Users must review licensing before merging. Credential files and GitHub automation edits are blocked from publication; archive links/special files and large archives/patches are also rejected in this first version.

## Validation performed

- Unit and HTTP tests use mocked GitHub responses; no real PRs, branches or API credentials were used.
- Browser flow tested: token connection → sidebar repo selection → local integration result → diff/check review → explicit approval → new-branch draft PR.
- Tests cover session rotation, CSRF and host checks, account ownership, archive traversal/links, protected files, stale approval, stale default branch, worker credential isolation and no default-branch update/merge.
- Desktop and mobile UI checked, including no horizontal overflow at 390px.
- Live GitHub App login and real PR creation require your registered app or fine-grained token and have **not** been exercised against your account.

The registered app separates user authorization from installation permissions. The connection endpoint guides users through both. For newly registered apps, the installation setup URL returns to the connection endpoint; existing app installations are also detected by the sidebar while the GitHub window is open.
