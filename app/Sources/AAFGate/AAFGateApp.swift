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
        .defaultSize(width: 1400, height: 800)
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

    func revealReports(for run: CheckRun) {
        NSWorkspace.shared.activateFileViewerSelecting(run.reportURLs)
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
    @State private var selectedRunID: CheckRun.ID?

    private var displayedRun: CheckRun? {
        queue.completed.first { $0.id == selectedRunID } ?? queue.current
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Image(systemName: "waveform.badge.magnifyingglass")
                Text("AAF Gate").font(.title2.bold())
                Spacer()
                Button("Open AAF…") { model.openFiles() }
            }
            VStack(alignment: .leading, spacing: 10) {
                if let run = displayedRun, run.status != .running {
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
                Text(displayedRun?.aaf.lastPathComponent ?? "Drop an AAF to check")
                    .font(.headline).textSelection(.enabled)
                if let run = displayedRun, !run.detail.isEmpty {
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
            .padding(16)
            .frame(maxWidth: .infinity, minHeight: 130, alignment: .leading)
            .background(isTargeted ? Color.accentColor.opacity(0.15) : Color.secondary.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            if let run = displayedRun, run.status != .running {
                if let message = run.reportSaveMessage {
                    Text(message).foregroundStyle(.orange).textSelection(.enabled)
                        .accessibilityIdentifier("report-save-error")
                } else if !run.reportURLs.isEmpty {
                    Button("Reveal reports") { model.revealReports(for: run) }
                        .accessibilityIdentifier("reveal-reports")
                }
                if case .finished = run.status {
                    FindingsView(findings: run.findings).id(run.id)
                }
            }
            if !queue.pending.isEmpty {
                Text("Queued: \(queue.pending.map(\.lastPathComponent).joined(separator: ", "))")
                    .foregroundStyle(.secondary)
            }
            if let error = queue.logRetentionError { Text(error).foregroundStyle(.secondary) }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(queue.completed) { result in
                        Button {
                            selectedRunID = result.id
                        } label: {
                            HStack {
                                Text(label(for: result.status)).bold().foregroundStyle(color(for: result.status))
                                Text(result.aaf.lastPathComponent)
                            }
                        }
                        .buttonStyle(.plain)
                        .help("Show this run's findings and reports")
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }.frame(maxHeight: 60)
        }
        .padding(24)
        .frame(minWidth: 1100, minHeight: 620)
        .onChange(of: queue.current?.id) { selectedRunID = nil }
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
