# minimax-web-extension — companion scripts

A few real-world examples that drive the bridge end-to-end.

- `leadgen.py` — Google search for "web developer wanted Kenya", extract the top 5 Facebook / Reddit posts, visit each, extract contact info.
- `find_bounties.py` — open GitHub's bounty-labeled issues, extract titles, repos, money tags, and the body of the top 3.

Run any of them with the server + extension already running:

```bash
python examples/leadgen.py
python examples/find_bounties.py
```

Both drive the user's actual browser via the bridge — they do **not** launch a new browser. Whatever session you have logged in is what gets used.
