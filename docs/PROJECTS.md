# Projects

A project is a goal with steps: a residence permit renewal, a school application, a move. One
YAML file per project in `projects/`. While Butler runs on the example settings, it shows the
invented examples in `examples/projects/`, and the first change you make from the portal or by
reply copies them into `projects/`. Once you have your own `config/settings.yaml`, the examples
never appear.

The morning brief shows each person's next steps ("Next up"). The weekly letter adds a line
per project on where it stands. The portal's Projects page ranks projects by what can be done
now and parks the rest.

## A project file

```yaml
name: Residency
summary: >
  Alex's residence card renewal. The renewal can't be filed before the card's last 60 days.

steps:
  - id: gather-docs
    do: Gather the renewal documents (passport, current card, proof of address)
    short: "Renewal documents ready"
    who: Alex
    priority: high

  - id: book
    do: Book the renewal appointment online
    who: Alex
    after: [gather-docs]
    not_before: 2027-01-15
```

## Step fields

| Field | Meaning |
|---|---|
| `id` | Short unique name, used by `after` and in replies |
| `do` | What to do, as an instruction |
| `short` | Optional outcome name shown where other steps depend on this one |
| `who` | A person's name, `Both`, or anyone else (an attorney, a school) |
| `status` | `todo` (default), `waiting`, `paused` or `done` |
| `after` | Step ids that must be done first; until then the step waits |
| `not_before` | A date before which the step isn't actionable; shown as a countdown |
| `every` | Days between repeats; with `last_done`, the step comes back when it's due |
| `until` | A step id; when that step is done, this repeating step retires |
| `waiting_on` | Who you're waiting for (with `status: waiting`); `chase_after` brings it back |
| `priority` | `high`, `normal` (default) or `low` |
| `notes` | Context for you and for Claude. Never put ID numbers here |
| `last_done` | Set by Butler when a step is done or checked |

`paused` steps are parked on purpose and stay out of the brief.

## Updating

- In the portal: Done, Pause, Checked today, or "Send an update" in your own words.
- By reply: "done: school visit", "waiting on the clinic until the 20th", "checked the
  smoke alarms". Claude maps it to a step; Python makes the change.
- In the file: edit freely. Butler edits only the lines it changes, so comments survive.

Every update is logged on the step and shown in the portal.
