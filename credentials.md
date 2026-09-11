# Credential model

The central mechanism of Loftline. Everything else is templating.

## The problem being solved

Enrolment with a vendor happens once and is not the recurring cost. The
recurring cost is **custody**: knowing which credentials exist, what each is
called, which environments consume it, and where its value lives. That
knowledge currently sits inside whichever project last used it, so every new
project pays to rediscover it.

The fix is a single vault holding account-level credentials, plus a descriptor
file recording everything about each one except its value. The descriptor is
written once per credential per lifetime. Acquisition instructions live in the
descriptor, so the answer to "how do I get another one of these" is never
looked up twice.

## Four states

Every credential a project needs resolves to exactly one state.

| State | Meaning | Action |
| --- | --- | --- |
| `held` | Account-level, already in the vault | Injected silently |
| `derivable` | Obtainable from something already held | Generated automatically |
| `manual` | Not derivable, or expired | Prompt with printed instructions, store permanently |
| `produced` | Created by provisioning, cannot pre-exist | Deferred to provision time |

`produced` is the state most easily missed. A managed database instance's URI
and password do not exist until the instance does; they are generated during
`terraform apply` or a vendor API call and written straight to environment
secrets, never to the vault.

On a first project the manual list is long. On a second it is empty unless the
spec enables a previously unused feature or a credential has expired.

## Descriptor schema

Held in `credentials.yml`, tracked in the repository. **Values never appear
here.** The descriptor records everything about a credential except its
secret.

```yaml
expo_push_token:
  vendor: expo
  scope: account                 # account | project
  state: held
  consumed_by: [backend]
  environments: [staging, production]
  github_secret: EXPO_PUSH_TOKEN
  vault_path: loftline/expo/push_token
  expires: null

apple_app_specific_password:
  vendor: apple
  scope: account
  state: manual
  consumed_by: [ci]
  environments: [production]
  github_secret: APPLE_APP_PASSWORD
  vault_path: loftline/apple/app_specific_password
  expires: 365d
  acquire: |
    1. appleid.apple.com, sign in
    2. Sign-In and Security, then App-Specific Passwords
    3. Generate, label it Loftline
    4. Copy immediately; the value is shown once and never again

neo4j_password:
  vendor: aura
  scope: project
  state: produced
  produced_by: aura_instance_create
  consumed_by: [backend]
  environments: [staging, production]
  github_secret: NEO4J_PASSWORD
  expires: null
```

Required fields on every entry: `vendor`, `scope`, `state`, `consumed_by`,
`environments`, `github_secret`. Required on `manual`: `acquire`. Required on
`produced`: `produced_by`. Required on `held` and `manual`: `vault_path`.

## Feature requirements

Features declare what they need. The resolver unions the requirements of every
enabled feature in the spec.

```yaml
base:
  requires: [jwt_secret, smtp_user, smtp_password]

notifications:
  requires: [expo_push_token]

mobile:
  requires: [expo_access_token, apple_team_id, app_store_connect_key, apple_app_specific_password]

database.aura:
  requires: [aura_client_id, aura_client_secret]
  produces: [neo4j_uri, neo4j_password]

database.postgres:
  produces: [database_url]

hosting.render:
  requires: [render_api_key]
  produces: [render_service_id]
```

## Resolver contract

Pure function. No network, no vendor calls, no side effects.

```
resolve(spec, credentials_yml, vault_index) -> Resolution

Resolution:
  inject:  [(name, vault_path, github_secret, environments)]
  derive:  [(name, derivation)]
  request: [(name, acquire_instructions, vault_path)]
  defer:   [(name, produced_by, github_secret, environments)]
```

`vault_index` is a list of paths present in the vault, not values. The resolver
never sees a secret. This is what makes it testable without any vendor account
and it is the first thing to be written.

Expiry is evaluated here: a credential in the vault whose `expires` has elapsed
relative to its stored timestamp moves from `inject` to `request`.

## Population strategy

Do not sit down and enumerate every credential from memory. Add each descriptor
the first time the resolver reports it missing. The inventory is then produced
as a by-product of work that was happening anyway, and it is complete by
construction rather than complete by recollection.

## Invariants

1. No credential value is ever committed to this repository.
2. No credential value is ever printed to logs or to CI output.
3. The resolver never reads values, only the index of paths.
4. `produced` credentials are written to environment secrets and never to the
   vault, because they belong to a project rather than to the account.
5. Terraform state is treated as sensitive regardless of what is in it, and the
   backend is private and encrypted.

---

## Vault backend

SOPS with age. See ADR-011 for the reasoning and the accepted risks.

### File layout

The encrypted vault lives in a **separate private repository**, never in this
one. The adapter reads it by path, supplied as `LOFTLINE_VAULT` or a config
value.

```yaml
loftline:
  render:
    api_key:
      value: ENC[AES256_GCM,data:...]
      acquired_at: 2026-08-18T10:04:00Z
  expo:
    push_token:
      value: ENC[AES256_GCM,data:...]
      acquired_at: 2026-08-18T10:06:00Z
```

`.sops.yaml` in that repository:

```yaml
creation_rules:
  - path_regex: vault\.ya?ml$
    encrypted_regex: '^value$'
    age: age1yourpublickey...,age1yourbackupkey...
```

Two recipients, always. The second is a backup identity stored elsewhere;
losing the sole key destroys the vault with no recovery path.

### Why `encrypted_regex: '^value$'` matters

SOPS encrypts leaf values and leaves keys and structure in plaintext. With only
`value` fields encrypted:

- `list_paths` parses the encrypted file directly. No decryption, no key
  required, no secret in memory.
- `acquired_at` is readable in plaintext, so expiry is evaluated without
  decryption.
- Only `get` decrypts, and only one extracted value at a time.

The resolver is therefore pure by construction rather than by discipline: it
consumes `list_paths` and timestamps, both of which are available from
ciphertext.

### Adapter operations

```
list_paths() -> list[str]
    Parse the encrypted YAML. Walk to every node containing a `value` key.
    Return slash-joined paths, e.g. "loftline/render/api_key".
    Never shells out to sops. Never decrypts.

get(path) -> str
    sops -d --extract '["loftline"]["render"]["api_key"]["value"]' vault.yml
    Single value to stdout. Never decrypt the whole document.

set(path, value) -> None
    sops set '["loftline"]["render"]["api_key"]' '{"value":"...","acquired_at":"..."}' vault.yml
    In-place. Plaintext must never touch disk, so no decrypt-edit-encrypt
    round trip through a temporary file.

acquired_at(path) -> datetime | None
    Read from the parsed ciphertext. No decryption.
```

Slash paths in descriptors map to SOPS extract expressions by splitting on `/`.

### Operational preconditions

Enforced by `loftline doctor`, which refuses to run other commands if any fail:

- `sops` and `age` present on PATH
- age key file exists and is mode 600
- vault file resolves and parses
- `.sops.yaml` lists at least two recipients
- full-disk encryption enabled

Full-disk encryption is a prerequisite rather than a recommendation. Without
it the age key is readable straight off the drive by anyone holding the
machine, and every other control is decorative.