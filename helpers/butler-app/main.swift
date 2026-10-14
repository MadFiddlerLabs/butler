// The Butler app (~/Applications/Butler.app): the way back to your butler without typing a
// command. It opens Terminal on ~/Butler/scripts/butler.command, which shows where you are in the
// course and the one thing to do next. If the Butler folder is gone, it opens the course's help
// page instead. A compiled binary rather than a script, so it signs and notarizes like the
// Calendar helper (packaging/macos/build-app.sh).
import Foundation

let home = FileManager.default.homeDirectoryForCurrentUser
let script = home.appendingPathComponent("Butler/scripts/butler.command")
let open = Process()
open.executableURL = URL(fileURLWithPath: "/usr/bin/open")
if FileManager.default.fileExists(atPath: script.path) {
    open.arguments = ["-a", "Terminal", script.path]
} else {
    open.arguments = ["https://buildyourbutler.com/support/"]
}
do {
    try open.run()
    open.waitUntilExit()
    exit(open.terminationStatus)
} catch {
    exit(1)
}
