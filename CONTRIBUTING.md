# Contributing

Thank you for improving Credit Monitoring. For bugs and feature requests, start with the appropriate [issue form](https://github.com/100xopensource/100x-credit-monitoring/issues/new/choose). For usage and security channels, see [SUPPORT.md](SUPPORT.md).

Participation is governed by our [Code of Conduct](CODE_OF_CONDUCT.md).

Before opening a change:

1. Describe the user or release-contract problem it solves.
2. Keep borrower data, client identifiers, credentials, private incident details, and generated monitoring outputs out of commits and issues.
3. Preserve read-only source handling, portable persisted paths, application-preserved/integrity-checked completed runs, and analyst approval boundaries.
4. Update public documentation when behavior changes.
5. Use fictional or approved synthetic examples only.

## Build and validate

Requirements: Python 3.10+, dependencies from `requirements.txt`, and LibreOffice for workbook formula recalculation. Install the Python dependency with:

```sh
python3 -m pip install -r requirements.txt
```

Verify that a deterministic rebuild matches the published checksum:

```sh
python3 tools/package.py check
```

Build the ignored local artifact when needed:

```sh
python3 tools/package.py build
```

Release artifacts are published through GitHub Releases rather than committed to the source tree.

Open a pull request and state the commands and manual checks you ran. By contributing, you agree that your contribution is licensed under Apache-2.0 and you certify its origin by signing off your commits (below).


## Sign your commits (DCO)

Credit Monitoring uses the [Developer Certificate of Origin](https://developercertificate.org/).
It is a one-line statement that you wrote the contribution, or otherwise have
the right to submit it under Apache-2.0. There is no contributor licence
agreement to sign.

Add the sign-off to every commit:

```sh
git commit -s -m "your message"
```

That appends a trailer using your `user.name` and `user.email`:

```
Signed-off-by: Your Name <you@example.com>
```

To add it to commits you have already made on a branch:

```sh
git rebase --signoff origin/main
```

Pull requests with unsigned commits will be asked to sign off before merge.
