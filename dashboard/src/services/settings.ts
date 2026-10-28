// The Settings page reads system/provider configuration status and triggers
// connection tests. There is no separate backend concept of "settings"
// beyond what /system/status and /system/integrations/*/test already
// expose, so this module simply re-exports them under the expected name.
export { getSystemStatus, testIntegration } from "@/services/system";
