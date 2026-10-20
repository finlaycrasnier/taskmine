// swift-tools-version:6.0
// The menu-bar reader. TaskmineCore holds the testable parts: hide, code, tidy,
// surfaces, sightings, edit bursts, the queue and the wire types. Taskmine is the app.
import Foundation
import PackageDescription

// Sources/TaskmineCore/Rules mirrors data/rules, and Tests/TaskmineCoreTests/Fixtures
// mirrors data/fixtures. SwiftPM copies a symlinked resource folder as a symlink, which
// breaks inside the built bundle, so these are copies. MirrorTests fails when they drift.

// The tests use Swift Testing. With only the Command Line Tools installed (no Xcode),
// SwiftPM does not find Testing.framework on its own, so add its search and runtime paths.
let commandLineTools = "/Library/Developer/CommandLineTools"
let cltDeveloper = commandLineTools + "/Library/Developer"

func activeDeveloperDirectory() -> String {
    if let dir = ProcessInfo.processInfo.environment["DEVELOPER_DIR"], !dir.isEmpty { return dir }
    if let dir = try? FileManager.default.destinationOfSymbolicLink(atPath: "/var/db/xcode_select_link") {
        return dir
    }
    return FileManager.default.fileExists(atPath: "/Applications/Xcode.app") ? "/Applications/Xcode.app" : commandLineTools
}

let onlyCommandLineTools = activeDeveloperDirectory().hasPrefix(commandLineTools)
    && FileManager.default.fileExists(atPath: cltDeveloper + "/Frameworks/Testing.framework")

let testSwiftSettings: [SwiftSetting] = onlyCommandLineTools
    ? [.unsafeFlags(["-F", cltDeveloper + "/Frameworks"])] : []
let testLinkerSettings: [LinkerSetting] = onlyCommandLineTools
    ? [.unsafeFlags([
        "-F", cltDeveloper + "/Frameworks",
        "-Xlinker", "-rpath", "-Xlinker", cltDeveloper + "/Frameworks",
        "-Xlinker", "-rpath", "-Xlinker", cltDeveloper + "/usr/lib",
    ])] : []

let package = Package(
    name: "Taskmine",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "TaskmineCore", targets: ["TaskmineCore"]),
        .executable(name: "Taskmine", targets: ["Taskmine"]),
    ],
    targets: [
        .target(
            name: "TaskmineCore",
            path: "Sources/TaskmineCore",
            resources: [.copy("Rules")]
        ),
        .executableTarget(
            name: "Taskmine",
            dependencies: ["TaskmineCore"],
            path: "Sources/Taskmine"
        ),
        .testTarget(
            name: "TaskmineCoreTests",
            dependencies: ["TaskmineCore"],
            path: "Tests/TaskmineCoreTests",
            resources: [.copy("Fixtures")],
            swiftSettings: testSwiftSettings,
            linkerSettings: testLinkerSettings
        ),
    ]
)
