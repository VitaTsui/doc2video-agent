import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
// Scenes are generated code, and a broken one shows up as a blank scene rather
// than as a failed render. Surfacing browser errors is what makes that visible
// in the log instead of eight silent seconds in the finished film.
Config.setChromiumOpenGlRenderer("angle");
Config.setLevel("info");
