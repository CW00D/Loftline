# Setting up Loftline on your machine

About half an hour, most of it making an encryption key and installing three
small tools. At the end you have a private vault for your credentials, the
`loftline` command, and Loftline available to Claude Desktop or Claude Code
as a set of tools.

Everything here runs on your own machine. Nothing is hosted, and no
credential value ever leaves your laptop except to the service it belongs
to (GitHub, when Loftline writes it as an environment secret).

## 1. Tools

| Tool | Why | Windows | macOS |
| --- | --- | --- | --- |
| Python 3.12 or later | Loftline is Python | python.org installer | `brew install python` |
| `uv` | Installs Loftline and its dependencies | `pip install uv` | `brew install uv` |
| `sops` and `age` | Encrypt the vault | `choco install sops age.portable` | `brew install sops age` |
| GitHub CLI `gh` | Writes secrets to your repositories | `choco install gh` | `brew install gh` |
| Docker Desktop | Runs generated projects locally | docker.com | docker.com |
| Terraform | Creates repositories (optional until you provision) | `choco install terraform` | `brew install terraform` |

On Windows the `choco` commands need an elevated PowerShell. Open a new
terminal afterwards so the PATH updates.

Then sign the GitHub CLI in:

```
gh auth login --hostname github.com --git-protocol https --web
```

## 2. Loftline itself

```
git clone https://github.com/CW00D/Loftline.git
cd Loftline
python -m uv sync
```

`python -m uv run loftline --help` should list the commands. If `uv` is on
your PATH, `uv run loftline` works too; the rest of this guide writes it the
short way.

## 3. Your encryption key, and its backup

The vault is encrypted to two keys: one on this machine, one kept somewhere
else. **Losing both means every credential in the vault is gone for good,
with no recovery path.** That is the trade for having no vendor involved.

```
# Windows (PowerShell)
New-Item -ItemType Directory -Force "$env:APPDATA\sops\age" | Out-Null
age-keygen -o "$env:APPDATA\sops\age\keys.txt"
age-keygen -o "$env:USERPROFILE\loftline-backup-key.txt"

# macOS
mkdir -p ~/Library/Application\ Support/sops/age
age-keygen -o ~/Library/Application\ Support/sops/age/keys.txt
age-keygen -o ~/loftline-backup-key.txt
```

Each command prints a line starting `Public key: age1...`. Keep both public
keys; the next step needs them. Then open the backup key file, copy its
contents into your password manager as a secure note, and delete the file.

The locations above are where `sops` looks by default on each platform.
`loftline doctor` tells you if it is somewhere else.

## 4. The vault

A vault is one encrypted file in its own private git repository. Loftline
creates it:

```
loftline vault init ../my-vault --recipient age1YOUR_KEY --recipient age1YOUR_BACKUP_KEY
```

That writes the SOPS configuration, an empty encrypted vault, a `.gitignore`
that keeps key material out, and initialises git. Then tell Loftline where
it is, permanently:

```
# Windows (PowerShell)
[Environment]::SetEnvironmentVariable("LOFTLINE_VAULT", "C:\full\path\to\my-vault\vault.yml", "User")

# macOS (add to ~/.zshrc)
export LOFTLINE_VAULT=/full/path/to/my-vault/vault.yml
```

Open a new terminal and check everything:

```
loftline doctor
```

Every line should read `ok`. Full-disk encryption may read `?` on Windows,
which means it could not be checked without an elevated shell; confirm it
is on by hand, because your key sits on disk protected by nothing else.

Finally, make the vault survive your laptop: create an empty **private**
repository on GitHub, then from the vault directory:

```
git remote add origin https://github.com/YOU/my-vault.git
git push -u origin main
```

Push again after every credential you add.

## 5. Your first credentials

Loftline never asks you to enumerate credentials up front. Describe a
project and ask what it needs:

```
loftline plan examples/skeleton.yml
```

The `Acquire` section lists what you must go and get, with the steps for
each. Store each one as you get it:

```
loftline vault set render_api_key
```

It prompts for the value with typing hidden. The value never appears on
screen, in your shell history, or in a file. Run `plan` again and the
credential has moved from `Acquire` to `Inject`.

## 6. Claude

Loftline is a set of tools Claude can call: explain the questions a project
answers, plan a project's credentials, generate it, write its secrets. No
tool ever takes a credential value; storing one stays a terminal command.

Print the configuration for your machine and paste it where your client
reads it:

```
loftline mcp config --desktop     # Claude Desktop: claude_desktop_config.json
loftline mcp config --code        # Claude Code: .mcp.json in a project
```

Claude Desktop's file is at `%APPDATA%\Claude\claude_desktop_config.json`
on Windows and `~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS. Add the printed `loftline` entry under `mcpServers` and restart
Desktop. Then ask it: "What does a Loftline project need?"

## What you now have

- A private, encrypted vault of your account-level credentials, backed up to
  GitHub, readable only with your key.
- `loftline plan`, which tells any project what it needs and what you
  already hold.
- `loftline new`, which generates a project, and `loftline secrets write`,
  which puts its credentials into GitHub for CI.
- The same, as tools inside Claude.

## If something is wrong

`loftline doctor` first, always. Every command checks the same
preconditions and names the one that failed.
