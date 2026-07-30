#include "aliver_event_sink.h"

#include <sstream>
#include <vector>

namespace {
std::string WindowsError(DWORD code) {
    LPSTR buffer = nullptr;
    const DWORD size = FormatMessageA(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr,
        code,
        MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
        reinterpret_cast<LPSTR>(&buffer),
        0,
        nullptr);
    std::string message = size && buffer ? std::string(buffer, size) : "Windows error " + std::to_string(code);
    if (buffer) LocalFree(buffer);
    return message;
}

std::wstring Quote(const std::wstring& value) {
    return L"\"" + value + L"\"";
}
}  // namespace

AliverEventSink::~AliverEventSink() {
    Stop();
}

bool AliverEventSink::Start(
    const std::wstring& collector_exe,
    const std::wstring& config_path,
    std::string* error) {
    Stop();

    SECURITY_ATTRIBUTES attributes{};
    attributes.nLength = sizeof(attributes);
    attributes.bInheritHandle = TRUE;

    HANDLE stdin_read = nullptr;
    if (!CreatePipe(&stdin_read, &stdin_write_, &attributes, 0)) {
        if (error) *error = WindowsError(GetLastError());
        return false;
    }
    SetHandleInformation(stdin_write_, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = stdin_read;
    startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);

    PROCESS_INFORMATION process{};
    std::wstring command = Quote(collector_exe) + L" --config " + Quote(config_path);
    std::vector<wchar_t> command_buffer(command.begin(), command.end());
    command_buffer.push_back(L'\0');

    const BOOL ok = CreateProcessW(
        collector_exe.c_str(),
        command_buffer.data(),
        nullptr,
        nullptr,
        TRUE,
        CREATE_NO_WINDOW,
        nullptr,
        nullptr,
        &startup,
        &process);
    CloseHandle(stdin_read);

    if (!ok) {
        if (error) *error = WindowsError(GetLastError());
        CloseHandle(stdin_write_);
        stdin_write_ = nullptr;
        return false;
    }

    process_ = process.hProcess;
    thread_ = process.hThread;
    return true;
}

bool AliverEventSink::SendJsonLine(const char* data, std::size_t size, std::string* error) {
    if (!Running() || !stdin_write_) {
        if (error) *error = "ALiver collector process is not running";
        return false;
    }
    std::string line(data, size);
    line.push_back('\n');
    DWORD written = 0;
    if (!WriteFile(stdin_write_, line.data(), static_cast<DWORD>(line.size()), &written, nullptr) ||
        written != line.size()) {
        if (error) *error = WindowsError(GetLastError());
        return false;
    }
    FlushFileBuffers(stdin_write_);
    return true;
}

void AliverEventSink::Stop() {
    if (stdin_write_) {
        CloseHandle(stdin_write_);
        stdin_write_ = nullptr;
    }
    if (thread_) {
        CloseHandle(thread_);
        thread_ = nullptr;
    }
    if (process_) {
        WaitForSingleObject(process_, 1500);
        if (WaitForSingleObject(process_, 0) == WAIT_TIMEOUT) {
            TerminateProcess(process_, 0);
        }
        CloseHandle(process_);
        process_ = nullptr;
    }
}

bool AliverEventSink::Running() const {
    return process_ && WaitForSingleObject(process_, 0) == WAIT_TIMEOUT;
}
