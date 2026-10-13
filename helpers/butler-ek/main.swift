// butler-ek: a tiny EventKit bridge for Butler.
// Reads calendar events and reads/writes Apple Reminders. Prints JSON to stdout.
// It can add and complete, never edit or delete: Butler's promise that nothing it does
// removes anything holds all the way down to this helper.
//
//   butler-ek calendars
//   butler-ek events --start 2026-10-09T00:00:00+02:00 --end 2026-10-16T00:00:00+02:00
//   butler-ek reminder-lists
//   butler-ek reminders --list Shopping
//   butler-ek ensure-list --list Butler
//   butler-ek add-reminder --list Shopping --title "Milk" [--notes "..."] [--due 2026-10-10]
//   butler-ek complete-reminder --id REMINDER_ID
//   butler-ek completed --list Butler --start 2026-09-01 --end 2026-10-01
//   butler-ek add-event --calendar "Family" --title T --start ISO --end ISO [--all-day 1] [--location L] [--notes N]

import Darwin
import EventKit
import Foundation

// macOS asks the "responsible" process for Calendar access: the app that started this helper
// (Terminal, Claude Code), or launchd's job at 07:00. A grant made in Terminal then may not cover
// the morning run. So, first thing, the helper runs itself again with responsibility disclaimed:
// macOS then asks for butler-ek, and one grant works whatever starts it. The call is private but
// long used in libSystem. If it's missing or the re-run fails, the helper carries on as before.
// BUTLER_EK_DISCLAIMED stops the second run from running itself again.
let disclaimedKey = "BUTLER_EK_DISCLAIMED"

func disclaimResponsibility() {
    guard getenv(disclaimedKey) == nil else { return }
    typealias SetDisclaim = @convention(c) (UnsafeMutablePointer<posix_spawnattr_t?>, Int32) -> Int32
    // RTLD_DEFAULT is (void *)-2; the macro doesn't reach Swift.
    guard let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "responsibility_spawnattrs_setdisclaim")
    else { return }
    var size: UInt32 = 0
    _ = _NSGetExecutablePath(nil, &size)
    var path = [CChar](repeating: 0, count: Int(size) + 1)
    guard _NSGetExecutablePath(&path, &size) == 0 else { return }
    var attributes: posix_spawnattr_t?
    guard posix_spawnattr_init(&attributes) == 0 else { return }
    defer { posix_spawnattr_destroy(&attributes) }
    // POSIX_SPAWN_SETEXEC replaces this process: same pid, arguments, stdin, stdout and stderr.
    guard posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETEXEC)) == 0,
          unsafeBitCast(symbol, to: SetDisclaim.self)(&attributes, 1) == 0
    else { return }
    setenv(disclaimedKey, "1", 1)
    var pid: pid_t = 0
    _ = posix_spawn(&pid, path, nil, &attributes, CommandLine.unsafeArgv, environ)
    unsetenv(disclaimedKey)   // only reached when the re-run failed
}

disclaimResponsibility()

let store = EKEventStore()

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

func emit(_ value: Any) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}

func requestAccess() {
    let sem = DispatchSemaphore(value: 0)
    var eventsOK = false
    var remindersOK = false
    store.requestFullAccessToEvents { ok, _ in eventsOK = ok; sem.signal() }
    sem.wait()
    store.requestFullAccessToReminders { ok, _ in remindersOK = ok; sem.signal() }
    sem.wait()
    if !eventsOK { fail("butler-ek: no Calendar access. Grant it in System Settings > Privacy & Security > Calendars.") }
    if !remindersOK { fail("butler-ek: no Reminders access. Grant it in System Settings > Privacy & Security > Reminders.") }
}

func option(_ name: String, _ args: [String]) -> String? {
    guard let i = args.firstIndex(of: "--" + name), i + 1 < args.count else { return nil }
    return args[i + 1]
}

let iso = ISO8601DateFormatter()
let dayOnly: DateFormatter = {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.dateFormat = "yyyy-MM-dd"
    return f
}()

func parseDate(_ s: String) -> Date {
    if let d = iso.date(from: s) { return d }
    if let d = dayOnly.date(from: s) { return d }
    fail("butler-ek: cannot parse date '\(s)'")
}

func reminderList(named name: String) -> EKCalendar? {
    store.calendars(for: .reminder).first { $0.title == name }
}

func calendarInfo(_ c: EKCalendar) -> [String: Any] {
    ["title": c.title, "source": c.source.title, "id": c.calendarIdentifier, "writable": c.allowsContentModifications]
}

func eventCalendar(named name: String) -> EKCalendar? {
    store.calendars(for: .event).first { $0.title == name && $0.allowsContentModifications }
}

func fill(_ e: EKEvent, _ args: [String]) {
    guard let title = option("title", args), let start = option("start", args), let end = option("end", args)
    else { fail("needs --title, --start and --end") }
    e.title = title
    e.isAllDay = option("all-day", args) == "1"
    e.startDate = parseDate(start)
    e.endDate = parseDate(end)
    e.location = option("location", args)
    e.notes = option("notes", args)
}

func declined(_ e: EKEvent) -> Bool {
    e.attendees?.first(where: { $0.isCurrentUser })?.participantStatus == .declined
}

// Who sent the invite: its address when EventKit gives one, and whether it's the person signed in
// on this Mac. Null for an event nobody invited anyone to. Butler marks invites from outside the
// household, so a stranger's text reaches Claude labelled as theirs.
func organizer(_ e: EKEvent) -> Any {
    guard let o = e.organizer else { return NSNull() }
    let link = o.url.absoluteString
    let email = link.lowercased().hasPrefix("mailto:")
        ? (String(link.dropFirst("mailto:".count)).removingPercentEncoding ?? "") : ""
    return ["email": email, "current_user": o.isCurrentUser]
}

// Every command, checked before asking for access, so a wrong one fails at once instead of
// waiting on a permission prompt.
let commands: Set<String> = ["calendars", "events", "reminder-lists", "reminders", "ensure-list",
                             "add-reminder", "complete-reminder", "completed", "add-event"]
let args = Array(CommandLine.arguments.dropFirst())
guard let command = args.first else { fail("usage: butler-ek <\(commands.sorted().joined(separator: "|"))> [options]") }
guard commands.contains(command) else { fail("butler-ek: unknown command '\(command)'") }
requestAccess()

switch command {
case "calendars":
    emit(store.calendars(for: .event).map(calendarInfo))

case "events":
    guard let start = option("start", args), let end = option("end", args) else { fail("events needs --start and --end") }
    // After a wake the local copy can be stale; ask accounts to sync, then give them a moment.
    store.refreshSourcesIfNecessary()
    Thread.sleep(forTimeInterval: Double(option("settle", args) ?? "8") ?? 8)
    store.reset()
    let predicate = store.predicateForEvents(withStart: parseDate(start), end: parseDate(end), calendars: nil)
    let events = store.events(matching: predicate).map { e -> [String: Any] in
        [
            "id": e.calendarItemIdentifier,
            "event_id": e.eventIdentifier ?? "",
            "occurrence": iso.string(from: e.occurrenceDate ?? e.startDate),
            "declined": declined(e),
            "title": e.title ?? "",
            "start": iso.string(from: e.startDate),
            "end": iso.string(from: e.endDate),
            "all_day": e.isAllDay,
            "recurring": e.hasRecurrenceRules,
            "location": e.location ?? "",
            "notes": String((e.notes ?? "").prefix(600)),
            "calendar": e.calendar.title,
            "source": e.calendar.source.title,
            "organizer": organizer(e),
        ]
    }
    emit(events)

case "reminder-lists":
    emit(store.calendars(for: .reminder).map(calendarInfo))

case "reminders":
    guard let name = option("list", args) else { fail("reminders needs --list") }
    guard let list = reminderList(named: name) else { fail("butler-ek: no Reminders list named '\(name)'") }
    let sem = DispatchSemaphore(value: 0)
    var out: [[String: Any]] = []
    let predicate = store.predicateForIncompleteReminders(withDueDateStarting: nil, ending: nil, calendars: [list])
    store.fetchReminders(matching: predicate) { items in
        for r in items ?? [] {
            var row: [String: Any] = ["id": r.calendarItemIdentifier, "title": r.title ?? "", "notes": r.notes ?? ""]
            if let due = r.dueDateComponents, let d = Calendar.current.date(from: due) { row["due"] = dayOnly.string(from: d) }
            out.append(row)
        }
        sem.signal()
    }
    sem.wait()
    emit(out)

case "ensure-list":
    guard let name = option("list", args) else { fail("ensure-list needs --list") }
    if let existing = reminderList(named: name) { emit(calendarInfo(existing)); break }
    // Create the list next to the existing iCloud lists so it syncs to phones.
    guard let source = store.calendars(for: .reminder).first(where: { $0.source.sourceType == .calDAV })?.source
        ?? store.defaultCalendarForNewReminders()?.source else { fail("butler-ek: no Reminders account found") }
    let list = EKCalendar(for: .reminder, eventStore: store)
    list.title = name
    list.source = source
    do { try store.saveCalendar(list, commit: true) } catch { fail("butler-ek: could not create list: \(error)") }
    emit(calendarInfo(list))

case "add-reminder":
    guard let name = option("list", args), let title = option("title", args) else { fail("add-reminder needs --list and --title") }
    guard let list = reminderList(named: name) else { fail("butler-ek: no Reminders list named '\(name)'") }
    let r = EKReminder(eventStore: store)
    r.calendar = list
    r.title = title
    r.notes = option("notes", args)
    if let due = option("due", args) {
        r.dueDateComponents = Calendar.current.dateComponents([.year, .month, .day], from: parseDate(due))
    }
    do { try store.save(r, commit: true) } catch { fail("butler-ek: could not save reminder: \(error)") }
    emit(["id": r.calendarItemIdentifier, "list": name, "title": title])

case "complete-reminder":
    guard let id = option("id", args), let r = store.calendarItem(withIdentifier: id) as? EKReminder
    else { fail("butler-ek: no reminder with that id") }
    r.isCompleted = true
    do { try store.save(r, commit: true) } catch { fail("butler-ek: could not complete reminder: \(error)") }
    emit(["completed": id, "title": r.title ?? ""])

case "completed":
    guard let name = option("list", args), let start = option("start", args), let end = option("end", args)
    else { fail("completed needs --list, --start and --end") }
    guard let list = reminderList(named: name) else { fail("butler-ek: no Reminders list named '\(name)'") }
    let sem = DispatchSemaphore(value: 0)
    var out: [[String: Any]] = []
    let predicate = store.predicateForCompletedReminders(withCompletionDateStarting: parseDate(start),
                                                          ending: parseDate(end), calendars: [list])
    store.fetchReminders(matching: predicate) { items in
        for r in items ?? [] {
            out.append(["title": r.title ?? "", "completed": r.completionDate.map { iso.string(from: $0) } ?? ""])
        }
        sem.signal()
    }
    sem.wait()
    emit(out)

case "add-event":
    guard let name = option("calendar", args), let cal = eventCalendar(named: name) else { fail("butler-ek: no writable calendar named '\(option("calendar", args) ?? "")'") }
    let e = EKEvent(eventStore: store)
    e.calendar = cal
    fill(e, args)
    do { try store.save(e, span: .thisEvent, commit: true) } catch { fail("butler-ek: could not save event: \(error)") }
    emit(["event_id": e.eventIdentifier ?? "", "calendar": name, "title": e.title ?? ""])

default:
    fail("butler-ek: unknown command '\(command)'")
}
