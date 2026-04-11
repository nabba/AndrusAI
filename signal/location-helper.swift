#!/usr/bin/env swift
//
// location-helper.swift — CoreLocation CLI for BotArmy spatial awareness.
//
// Requests a single location fix, prints JSON to stdout, exits.
// First run triggers macOS permission dialog.
//
// Compile:  swiftc -o location-helper location-helper.swift -framework CoreLocation -framework Foundation
// Run:      ./location-helper
// Output:   {"lat": 60.1699, "lon": 24.9384, "accuracy": 10.5, "altitude": 12.3, "ts": "2026-04-10T16:00:00Z"}
//

import CoreLocation
import Foundation

class LocationDelegate: NSObject, CLLocationManagerDelegate {
    let semaphore = DispatchSemaphore(value: 0)
    var result: [String: Any]?

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        result = [
            "lat": loc.coordinate.latitude,
            "lon": loc.coordinate.longitude,
            "accuracy": loc.horizontalAccuracy,
            "altitude": loc.altitude,
            "ts": ISO8601DateFormatter().string(from: loc.timestamp)
        ]
        semaphore.signal()
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        let clError = error as? CLError
        result = ["error": error.localizedDescription, "code": clError?.code.rawValue ?? -1]
        semaphore.signal()
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        switch manager.authorizationStatus {
        case .denied, .restricted:
            result = ["error": "Location permission denied"]
            semaphore.signal()
        case .authorizedAlways:
            manager.requestLocation()
        case .notDetermined:
            // Will be called again after user responds to permission dialog
            break
        @unknown default:
            break
        }
    }
}

let manager = CLLocationManager()
let delegate = LocationDelegate()
manager.delegate = delegate
manager.desiredAccuracy = kCLLocationAccuracyHundredMeters

// Request permission (shows dialog on first run)
// On macOS, requestAlwaysAuthorization is the only option
manager.requestAlwaysAuthorization()

// If already authorized, request immediately
if manager.authorizationStatus == .authorizedAlways {
    manager.requestLocation()
}

// Wait up to 10 seconds for a fix
let timeout = delegate.semaphore.wait(timeout: .now() + 10)

if timeout == .timedOut {
    let output: [String: Any] = ["error": "Timeout waiting for location (10s)"]
    if let data = try? JSONSerialization.data(withJSONObject: output),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    }
    exit(1)
}

if let result = delegate.result {
    if let data = try? JSONSerialization.data(withJSONObject: result),
       let str = String(data: data, encoding: .utf8) {
        print(str)
        exit(result["error"] != nil ? 1 : 0)
    }
}

print("{\"error\": \"No result\"}")
exit(1)
