# Secrets in records — copy these wordings

The gate refuses a secret's value **and** anything derived from it: length, hash, size, prefix,
a mask. It allows exactly one statement: **whether the secret is configured**.

Do not invent a phrasing. Copy a line from the `allowed` blocks below, replacing only the names.
An adapted wording is the one that gets refused, and a refusal while copying the skill's own
advice reads as a broken check.

Why the line sits there: a length narrows a brute-force search, a hash can be checked offline, a
prefix names the key's format. None of them is needed to answer "is it set".

## What to write

### One secret, one line

```text allowed
api_key: configured
api_key: configured=false
token: configured
password: configured
```

### Several secrets at once

```text allowed
api_key: configured, token: configured, password: configured=false
```

### A whole provider

```text allowed
provider: openai, configured=yes
provider: anthropic, configured=no
```

### The same fact spelled in the name

```text allowed
api_key_configured=yes
token_configured=false
credentials_found=no
```

### Before and after a fix

`before` and `after` are the states, not the values — this is how a record shows that a change
made a difference without naming anything secret.

```text allowed
api_key: configured=false -> configured=true
api_key after fix: configured
```

### In a log line

```text allowed
[DEBUG] provider=openai api_key: configured
[DEBUG] token refreshed, token: configured
```

### In a results table cell

```text allowed
| api_key | configured | configured | — | ok |
```

### In the record's scan marker

```text allowed
Secrets scan: clean
```

## What not to write

Every line in this block is refused by the gate. They are shown so that an adapted phrase can be
recognised as one of them.

```text forbidden
api_key: sk-abcdef1234567890abcdef
api_key: len=51
api_key_len=51
api_key_length: 51
token: length=120
password_size=32
token_sha256=9f2c8b1a
api_key: sha256=deadbeef
secret_hash=abc123
token_md5=5d41402abc4b2a76b9719d911017c592
api_key_prefix=sk-proj
token_suffix=9f2c
api_key: bytes=51
api_key: sk-…abcd
Authorization: Bearer abcdefghijklmnop
```

## If a fact cannot be stated this way

Some questions genuinely need more than "configured" — which provider, which environment, whether
the value changed since the last run. Ask in those terms:

| Instead of | Write |
|---|---|
| "the key is 51 characters" | `api_key: configured` |
| "the token ends in 9f2c" | `token: configured` |
| "sha256 of the secret is …" | `api_key: configured` (a hash proves nothing an operator needs) |
| "which provider?" | `provider: openai, configured=yes` |
| "did it change?" | `api_key: configured=false -> configured=true` |

If none of these expresses what the run needs, that is a finding for the operator — say what is
missing, and do not encode it in a derived form.
