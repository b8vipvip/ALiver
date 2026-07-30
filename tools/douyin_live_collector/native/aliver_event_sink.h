#pragma once

#include <windows.h>

#include <cstddef>
#include <string>

class AliverEventSink {
public:
    AliverEventSink() = default;
    ~AliverEventSink();

    AliverEventSink(const AliverEventSink&) = delete;
    AliverEventSink& operator=(const AliverEventSink&) = delete;

    bool Start(const std::wstring& collector_exe, const std::wstring& config_path, std::string* error);
    bool SendJsonLine(const char* data, std::size_t size, std::string* error);
    void Stop();
    bool Running() const;

private:
    HANDLE process_ = nullptr;
    HANDLE thread_ = nullptr;
    HANDLE stdin_write_ = nullptr;
};
