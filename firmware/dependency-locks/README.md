# Firmware dependency locks

These ESP-IDF Component Manager lockfiles are selected by board profile,
firmware application, and active ESP-IDF version. Profiles that declare a
required SDK version must be regenerated only with that SDK loaded. Keeping
every lock here prevents a multi-profile rebuild from rewriting a shared
project-root lock between builds.
