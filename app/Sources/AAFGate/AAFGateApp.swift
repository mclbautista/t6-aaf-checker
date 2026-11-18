import AppKit
import SwiftUI
import UniformTypeIdentifiers
import AAFGateCore

@main
struct AAFGateApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = GateModel.shared

    var body: some Scene {
        Window("AAF Gate", id: "main") {
            GateView(model: model)
                .onOpenURL { model.enqueue([$0]) }
        }
        .defaultSize(width: 600, height: 420)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Open…") { model.openFiles() }
                    .keyboardShortcut("o")
            }
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    func application(_ application: NSApplication, open urls: [URL]) {
        GateModel.shared.enqueue(urls)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        sender.windows.first { $0.canBecomeMain }?.makeKeyAndOrderFront(nil)
        return true
    }
}

@MainActor
final class GateModel: ObservableObject {
    static let shared = GateModel()

    let queue = CheckQueue(
        runner: WorkerRunner(executableURL: Bundle.main.bundleURL
            .appendingPathComponent("Contents/Helpers/aaf-qc-worker/aaf-qc-worker")),
        logs: RunLogStore()
    )

    func openFiles() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "aaf") ?? .data]
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.begin { response in
            if response == .OK { self.enqueue(panel.urls) }
        }
    }

    func enqueue(_ urls: [URL]) {
        queue.enqueue(urls)
        NSApp.activate(ignoringOtherApps: true)
        NSApp.windows.first { $0.canBecomeMain }?.makeKeyAndOrderFront(nil)
    }

    func acceptDrop(_ providers: [NSItemProvider]) -> Bool {
        let files = providers.filter { $0.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) }
        guard !files.isEmpty else { return false }
        Task {
            var urls: [URL] = []
            for provider in files {
                let data: Data? = await withCheckedContinuation { continuation in
                    provider.loadDataRepresentation(forTypeIdentifier: UTType.fileURL.identifier) { data, _ in
                        continuation.resume(returning: data)
                    }
                }
                if let data, let url = URL(dataRepresentation: data, relativeTo: nil) { urls.append(url) }
            }
            enqueue(urls)
        }
        return true
    }

    func revealReports() {
        NSWorkspace.shared.activateFileViewerSelecting((queue.current?.reportPaths ?? []).map { URL(fileURLWithPath: $0) })
    }
}

struct GateView: View {
    let model: GateModel
    @ObservedObject var queue: CheckQueue

    init(model: GateModel) {
        self.model = model
        self.queue = model.queue
    }
    @State private var isTargeted = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Image(systemName: "waveform.badge.magnifyingglass")
                Text("AAF Gate").font(.title2.bold())
                Spacer()
                Button("Open AAF…") { model.openFiles() }
            }
            VStack(alignment: .leading, spacing: 10) {
                if let run = queue.current, run.status != .running {
                    Text(label(for: run.status))
                        .font(.system(size: 42, weight: .bold, design: .rounded))
                        .foregroundStyle(color(for: run.status))
                        .accessibilityIdentifier("verdict")
                } else if queue.isRunning {
                    HStack {
                        ProgressView().controlSize(.small)
                        Spacer()
                        Button(queue.isCancelling ? "Cancelling…" : "Cancel") { queue.cancel() }
                            .disabled(!queue.canCancel)
                            .accessibilityIdentifier("cancel")
                    }
                } else {
                    Image(systemName: "square.and.arrow.down").font(.largeTitle)
                }
                Text(queue.current?.aaf.lastPathComponent ?? "Drop an AAF to check")
                    .font(.headline).textSelection(.enabled)
                if let run = queue.current, !run.detail.isEmpty {
                    Text(run.detail).textSelection(.enabled)
                }
                if queue.isRunning, let progress = queue.current?.progress {
                    ProgressView(value: Double(progress.clipsDone), total: Double(max(1, progress.clipsTotal)))
                    Text("\(progress.clipsDone) of \(progress.clipsTotal) clips · \(ByteCountFormatter.string(fromByteCount: progress.bytesRead, countStyle: .file)) read")
                        .foregroundStyle(.secondary)
                        .accessibilityIdentifier("progress")
                }
                Text("Drop AAF files here").foregroundStyle(.secondary)
            }
            .padding(24)
            .frame(maxWidth: .infinity, minHeight: 180, alignment: .leading)
            .background(isTargeted ? Color.accentColor.opacity(0.15) : Color.secondary.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            if !(queue.current?.reportPaths.isEmpty ?? true) {
                Button("Reveal reports in Finder") { model.revealReports() }
            }
            if !queue.pending.isEmpty {
                Text("Queued: \(queue.pending.map(\.lastPathComponent).joined(separator: ", "))")
                    .foregroundStyle(.secondary)
            }
            if let error = queue.logRetentionError { Text(error).foregroundStyle(.secondary) }
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(queue.completed) { result in
                        HStack {
                            Text(label(for: result.status)).bold().foregroundStyle(color(for: result.status))
                            Text(result.aaf.lastPathComponent)
                        }
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(24)
        .frame(minWidth: 480, minHeight: 360)
        .onDrop(of: [UTType.fileURL], isTargeted: $isTargeted) { model.acceptDrop($0) }
    }

    private func label(for status: CheckStatus) -> String {
        switch status {
        case .running: "Checking"
        case .cancelled: "Cancelled"
        case .finished(let verdict): verdict.rawValue
        }
    }

    private func color(for status: CheckStatus) -> Color {
        switch status {
        case .finished(.pass): .green
        case .finished(.fail): .red
        case .finished(.error): .orange
        case .cancelled, .running: .secondary
        }
    }
}
