#!/usr/bin/env swift
//
// calendar_events.swift — Fast calendar event query via EventKit.
//
// AppleScript's `every event whose start date >= X` predicate is notoriously
// slow (10+ seconds per calendar even when empty) because Calendar.app walks
// every event in a calendar's history.  EventKit queries the underlying
// Calendar Store database directly and returns results in ~100ms per query.
//
// Usage:
//   swift calendar_events.swift list  --start ISO --end ISO [--calendar NAME]
//   swift calendar_events.swift calendars
//
// ISO format: "2026-04-20T00:00:00" or "2026-04-20 00:00:00"
//
// Output: JSON array of event objects, or error JSON with "error" key.
//
// Permissions: Requires Calendar access.  On first run the system prompts
// the user; subsequently the grant is remembered in System Settings >
// Privacy & Security > Calendars.
//

import Foundation
import EventKit

// MARK: - Output helpers

struct CalendarEvent: Codable {
    let calendar: String
    let title: String
    let start: String
    let end: String
    let location: String
    let allDay: Bool
    let notes: String
}

func emitJSON<T: Encodable>(_ value: T) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    if let data = try? encoder.encode(value),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    } else {
        print("{\"error\":\"json_encode_failed\"}")
    }
}

func emitError(_ message: String) {
    let payload: [String: String] = ["error": message]
    emitJSON(payload)
}

// MARK: - Date parsing

func parseISODate(_ s: String) -> Date? {
    let formats = [
        "yyyy-MM-dd'T'HH:mm:ss",
        "yyyy-MM-dd HH:mm:ss",
        "yyyy-MM-dd'T'HH:mm",
        "yyyy-MM-dd HH:mm",
        "yyyy-MM-dd",
    ]
    let fmt = DateFormatter()
    fmt.locale = Locale(identifier: "en_US_POSIX")
    fmt.timeZone = TimeZone.current
    for format in formats {
        fmt.dateFormat = format
        if let d = fmt.date(from: s) { return d }
    }
    return nil
}

func formatDate(_ d: Date) -> String {
    let fmt = DateFormatter()
    fmt.locale = Locale(identifier: "en_US_POSIX")
    fmt.timeZone = TimeZone.current
    fmt.dateFormat = "yyyy-MM-dd HH:mm"
    return fmt.string(from: d)
}

// MARK: - Permission handling

func requestAccessSync(store: EKEventStore) -> Bool {
    let sem = DispatchSemaphore(value: 0)
    var granted = false

    // iOS 17 / macOS 14+ uses requestFullAccessToEvents.  Older uses
    // requestAccess(to:).  Try the new API first, fall back to old.
    if #available(macOS 14.0, *) {
        store.requestFullAccessToEvents { ok, _ in
            granted = ok
            sem.signal()
        }
    } else {
        store.requestAccess(to: .event) { ok, _ in
            granted = ok
            sem.signal()
        }
    }

    _ = sem.wait(timeout: .now() + 10)
    return granted
}

// MARK: - Argument parsing

func parseArgs() -> (String, [String: String]) {
    let args = CommandLine.arguments
    guard args.count >= 2 else {
        emitError("usage: swift calendar_events.swift <list|calendars> [--start ISO] [--end ISO] [--calendar NAME]")
        exit(1)
    }
    let command = args[1]
    var kv: [String: String] = [:]
    var i = 2
    while i < args.count - 1 {
        let key = args[i].hasPrefix("--") ? String(args[i].dropFirst(2)) : args[i]
        let val = args[i + 1]
        kv[key] = val
        i += 2
    }
    return (command, kv)
}

// MARK: - Main

let (command, kv) = parseArgs()
let store = EKEventStore()

// Request access (sync, 10s timeout)
guard requestAccessSync(store: store) else {
    emitError("Calendar access denied. Grant in System Settings > Privacy & Security > Calendars.")
    exit(1)
}

switch command {
case "calendars":
    let calendars = store.calendars(for: .event)
    struct CalInfo: Codable { let title: String; let type: String; let allowsModify: Bool }
    let out = calendars.map { cal in
        CalInfo(
            title: cal.title,
            type: String(describing: cal.type.rawValue),
            allowsModify: cal.allowsContentModifications
        )
    }
    emitJSON(out)

case "list":
    guard let startStr = kv["start"], let endStr = kv["end"],
          let startDate = parseISODate(startStr),
          let endDate = parseISODate(endStr) else {
        emitError("list requires --start ISO --end ISO")
        exit(1)
    }

    let calendars: [EKCalendar]
    if let wanted = kv["calendar"] {
        calendars = store.calendars(for: .event).filter { $0.title == wanted }
        if calendars.isEmpty {
            emitError("Calendar '\(wanted)' not found")
            exit(1)
        }
    } else {
        calendars = store.calendars(for: .event)
    }

    // predicateForEvents is the EventKit equivalent of AppleScript's `whose`
    // — but it uses the indexed date store under the hood, so it's fast
    // regardless of calendar history size.
    let predicate = store.predicateForEvents(
        withStart: startDate, end: endDate, calendars: calendars
    )
    let events = store.events(matching: predicate)

    let out = events.map { evt in
        CalendarEvent(
            calendar: evt.calendar?.title ?? "?",
            title: evt.title ?? "(no title)",
            start: evt.startDate.map(formatDate) ?? "",
            end: evt.endDate.map(formatDate) ?? "",
            location: evt.location ?? "",
            allDay: evt.isAllDay,
            notes: evt.notes ?? ""
        )
    }
    emitJSON(out)

default:
    emitError("unknown command: \(command)")
    exit(1)
}
