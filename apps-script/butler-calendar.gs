/**
 * Optional calendar jobs for Butler. Runs in Google Apps Script every 5 minutes, whether or not
 * the Mac is awake.
 *
 * 1. Copy (optional): every event on a source calendar (except declined invites) is copied
 *    to the family calendar. Useful when a work calendar can't be shared outside its domain:
 *    run this script as the work account and give it "Make changes to events" on the family
 *    calendar. Copies carry title, time and location only. A private tag links each copy to
 *    its source, so later runs update or remove it. Guardrails stop mass deletes.
 * 2. Color: events with no color of their own get the owner's color, matched by name in the
 *    title. Colors someone set by hand are left alone.
 *
 * Setup: paste into a new Apps Script project (script.google.com) under the account that
 * owns the source calendar, fill in CONFIG, run install() once and approve access.
 * Leave `source` empty to only color events.
 */

const CONFIG = {
  source: '',                              // e.g. 'you@work.example.com'; '' = no copying
  target: 'family-calendar-id@group.calendar.google.com', // the family calendar's ID
  daysAhead: 60,
  maxDeletesPerRun: 10,
  // Google Calendar color ids: 9 Blueberry, 11 Tomato, 4 Flamingo, 10 Basil, 5 Banana, 6 Tangerine, 3 Grape.
  // The first person owns the source calendar: copied events get their color.
  people: [
    { name: 'Sam', aliases: ['Sam'], color: '9' },
    { name: 'Alex', aliases: ['Alex'], color: '11' },
    { name: 'Mia', aliases: ['Mia', 'Pat'], color: '10' },
  ],
};

const TAG = 'butlerCopyOf';
const COPY_NOTE = 'Copied from ' + CONFIG.source;

function install() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('sync').timeBased().everyMinutes(5).create();
  sync();
}

function sync() {
  const start = new Date();
  start.setDate(start.getDate() - 1);
  start.setHours(0, 0, 0, 0);
  const end = new Date(start.getTime() + (CONFIG.daysAhead + 1) * 86400000);
  // Color even if copying fails, then rethrow so Apps Script emails the failure.
  let copyError = null;
  if (CONFIG.source) {
    try { copyEvents_(start, end); } catch (e) { copyError = e; }
  }
  colorEvents_(start, end);
  if (copyError) throw copyError;
}

function key_(event) {
  // Occurrences of a series share an id; the start time tells them apart.
  return event.getId() + '|' + event.getStartTime().toISOString();
}

function declined_(event) {
  const me = event.getGuestByEmail(CONFIG.source);
  return me !== null && me.getGuestStatus() === CalendarApp.GuestStatus.NO;
}

function targetCalendar_() {
  // Apps Script only opens shared calendars the account has added to its list.
  const cal = CalendarApp.getCalendarById(CONFIG.target)
    || CalendarApp.subscribeToCalendar(CONFIG.target, { hidden: false });
  if (!cal) throw new Error('Family calendar not visible: check sharing');
  return cal;
}

function copyEvents_(start, end) {
  const source = CalendarApp.getCalendarById(CONFIG.source);
  const target = targetCalendar_();
  if (!source) throw new Error('Source calendar not visible');

  const wanted = {};
  source.getEvents(start, end).forEach(e => { if (!declined_(e)) wanted[key_(e)] = e; });

  const copies = {};
  target.getEvents(start, end).forEach(e => {
    const k = e.getTag(TAG);
    if (k) copies[k] = e;
  });

  Object.keys(wanted).forEach(k => {
    const src = wanted[k];
    const copy = copies[k];
    if (!copy) {
      const made = src.isAllDayEvent()
        ? target.createAllDayEvent(src.getTitle(), src.getAllDayStartDate(), src.getAllDayEndDate(),
                                   { location: src.getLocation(), description: COPY_NOTE })
        : target.createEvent(src.getTitle(), src.getStartTime(), src.getEndTime(),
                             { location: src.getLocation(), description: COPY_NOTE });
      made.setTag(TAG, k);
      made.setColor(CONFIG.people[0].color);
      return;
    }
    if (copy.getTitle() !== src.getTitle()) copy.setTitle(src.getTitle());
    if (copy.getLocation() !== src.getLocation()) copy.setLocation(src.getLocation());
    if (src.isAllDayEvent()) {
      if (!copy.isAllDayEvent() || +copy.getAllDayStartDate() !== +src.getAllDayStartDate()
          || +copy.getAllDayEndDate() !== +src.getAllDayEndDate()) {
        copy.setAllDayDates(src.getAllDayStartDate(), src.getAllDayEndDate());
      }
    } else if (copy.isAllDayEvent() || +copy.getStartTime() !== +src.getStartTime()
               || +copy.getEndTime() !== +src.getEndTime()) {
      copy.setTime(src.getStartTime(), src.getEndTime());
    }
  });

  const stale = Object.keys(copies).filter(k => !(k in wanted));
  if (stale.length && (Object.keys(wanted).length === 0 || stale.length > CONFIG.maxDeletesPerRun)) {
    console.warn('Skipped deleting ' + stale.length + ' copies: source may be unavailable');
    return;
  }
  stale.forEach(k => copies[k].deleteEvent());
}

function owner_(title) {
  const named = CONFIG.people.filter(p =>
    p.aliases.some(a => new RegExp('\\b' + a + '\\b', 'i').test(title)));
  return named.length === 1 ? named[0] : null;
}

function colorEvents_(start, end) {
  const target = targetCalendar_();
  target.getEvents(start, end).forEach(e => {
    if (e.getColor()) return; // someone chose a color; keep it
    const person = e.getTag(TAG) ? CONFIG.people[0] : owner_(e.getTitle());
    if (person) e.setColor(person.color);
  });
}
