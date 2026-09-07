#pragma once

namespace hexe::system {

void init_asset_sync();
bool asset_sync_active();
const char *asset_sync_status();
const char *asset_sync_manifest_version();
int asset_sync_checked_count();
int asset_sync_downloaded_count();
int asset_sync_failed_count();

}  // namespace hexe::system
