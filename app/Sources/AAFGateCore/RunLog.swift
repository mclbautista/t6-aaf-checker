import Darwin
import Foundation

public struct RunLogStore: Sendable {
    public static var defaultDirectory: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/AAF Gate", isDirectory: true)
    }

    public let directoryURL: URL
    private let now: @Sendable () -> Date

    public init(directoryURL: URL = Self.defaultDirectory, now: @escaping @Sendable () -> Date = { Date() }) {
        self.directoryURL = directoryURL
        self.now = now
    }

    public func removeExpiredLogs() throws {
        let manager = FileManager.default
        try manager.createDirectory(at: directoryURL, withIntermediateDirectories: true)
        let keys: Set<URLResourceKey> = [.isRegularFileKey, .isSymbolicLinkKey, .contentModificationDateKey]
        let cutoff = now().addingTimeInterval(-30 * 86400)
        for url in try manager.contentsOfDirectory(at: directoryURL, includingPropertiesForKeys: Array(keys)) {
            guard url.pathExtension == "log" else { continue }
            let values = try url.resourceValues(forKeys: keys)
            guard values.isRegularFile == true, values.isSymbolicLink != true,
                  let date = values.contentModificationDate, date < cutoff else { continue }
            try manager.removeItem(at: url)
        }
    }

    public func makeLog(for aaf: URL) throws -> WorkerLog {
        try FileManager.default.createDirectory(at: directoryURL, withIntermediateDirectories: true)
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyyMMdd-HHmmss-SSS"
        let stem = formatter.string(from: now()) + "_" + aaf.deletingPathExtension().lastPathComponent
        var suffix = 1
        while true {
            let name = stem + (suffix == 1 ? "" : "_\(suffix)") + ".log"
            let url = directoryURL.appendingPathComponent(name)
            let descriptor = Darwin.open(url.path, O_WRONLY | O_CREAT | O_EXCL, 0o600)
            if descriptor >= 0 {
                return WorkerLog(handle: FileHandle(fileDescriptor: descriptor, closeOnDealloc: true))
            }
            guard errno == EEXIST else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
            suffix += 1
        }
    }
}

/// Shared by the two pipe readers; each append is serialized and bounded.
public final class WorkerLog: @unchecked Sendable {
    private let lock = NSLock()
    private let handle: FileHandle
    private var writeError: String?

    fileprivate init(handle: FileHandle) { self.handle = handle }

    func append(_ data: Data) {
        lock.withLock {
            do { try handle.write(contentsOf: data) }
            catch { writeError = error.localizedDescription }
        }
    }

    var failure: String? { lock.withLock { writeError } }
}
