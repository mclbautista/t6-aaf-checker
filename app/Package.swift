// swift-tools-version: 6.2
import PackageDescription
import Foundation

// CLT ships this runtime outside SwiftPM's default library search paths.
let developerDirectory = ProcessInfo.processInfo.environment["DEVELOPER_DIR"]
    ?? "/Library/Developer/CommandLineTools"
let testingLibraries = developerDirectory + "/Library/Developer/usr/lib"

let package = Package(
    name: "AAFGate",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "AAFGateCore", targets: ["AAFGateCore"]),
        .executable(name: "AAFGate", targets: ["AAFGate"]),
    ],
    dependencies: [
        .package(url: "https://github.com/swiftlang/swift-testing.git", exact: "6.3.2"),
    ],
    targets: [
        .target(name: "AAFGateCore"),
        .executableTarget(name: "AAFGate", dependencies: ["AAFGateCore"]),
        .testTarget(name: "AAFGateCoreTests", dependencies: [
            "AAFGateCore", .product(name: "Testing", package: "swift-testing"),
        ], linkerSettings: [
            .unsafeFlags(["-L", testingLibraries, "-Xlinker", "-rpath", "-Xlinker", testingLibraries]),
        ]),
    ]
)
