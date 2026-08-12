# careers-plugins

A Claude Code plugin marketplace for the **careers vertical** — tools that help a person find
their next role, and help a small agency support several people doing the same.

## Plugins

| plugin | what it does | status |
|---|---|---|
| **jobsearch** | Sweeps your mailbox and LinkedIn for replies and interview invitations, screens roles against your own compensation floors and geography, tracks every application and conversation, and drafts outreach in your voice. It never sends anything itself. | available |

More are planned. Each plugin lives under `plugins/<name>/` and installs independently — you take
the ones you want.

## Install

1. Add this repository as a marketplace in Claude Code, then install a plugin from it with
   `/plugin`.
2. Follow that plugin's own `INSTALL.md` — for jobsearch, see
   [plugins/jobsearch/INSTALL.md](plugins/jobsearch/INSTALL.md).

Adding the marketplace from a **local path** registers it as a `directory` source. That works on
the machine you did it on and nowhere else — a clone or a cloud session will report
`Unknown command` for a correctly-spelled skill, with no error anywhere to explain it. Add it
from this repository's URL instead.

## Documentation

| | |
|---|---|
| [How it works](docs/user/jobsearch/how-it-works.md) | the engine/profile split, what runs when, the rules it holds itself to, and what it costs |
| [Your data](docs/user/jobsearch/your-data.md) | every file the plugin keeps, what the fields mean, and how to edit them by hand safely |
| [Reporting a problem](docs/user/reporting-issues.md) | how to tell a configuration problem from a defect, and what never to paste into a public tracker |

## Your data stays yours

Nothing personal lives in this repository, and nothing personal is sent anywhere.

A plugin here is **code only**. Your resume, contacts, compensation floors and search history live
in a directory **you** create and own, outside the plugin, and the two are never the same folder.
That separation is what makes it safe for this repository to be public and updated while your
search stays private.

Automated checks enforce it on every change, and a published version is verified against a real
profile before release rather than trusted because a test passed.

## Reporting a problem

Issues are open — please file one. **Read
[docs/user/reporting-issues.md](docs/user/reporting-issues.md) first**: it explains how to tell a
misconfiguration from a real defect, and what to strip before pasting output, since issues are
public and permanent.

Pull requests are not accepted. Changes are made by the maintaining team so that every release is
verified end to end; a report with a clear reproduction is far more useful here than a patch.

## License

Apache License 2.0 — see [LICENSE](LICENSE). Copyright 2026 Crinaro.
