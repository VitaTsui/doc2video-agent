import { Config } from "@remotion/cli/config";

// Slides are photographic-ish stills; JPEG previews keep render time down while
// the final encode stays H.264.
Config.setVideoImageFormat("jpeg");
Config.setCodec("h264");
Config.setPixelFormat("yuv420p");
Config.setOverwriteOutput(true);
// Deterministic output is the whole point of using a programmatic renderer.
Config.setChromiumDisableWebSecurity(false);
