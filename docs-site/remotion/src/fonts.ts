import { loadFont } from "@remotion/google-fonts/Inter";
import { loadFont as loadLocalFont } from "@remotion/fonts";
import { staticFile } from "remotion";

// Inter for body text
const { fontFamily, waitUntilDone } = loadFont("normal", {
  weights: ["400", "500", "600", "700"],
  subsets: ["latin"],
});

// Material Icons for sidebar/section icons (matches NiceGUI/Quasar)
const materialIconsLoaded = loadLocalFont({
  family: "Material Icons",
  url: staticFile("MaterialIcons-Regular.woff2"),
});

export { fontFamily, waitUntilDone, materialIconsLoaded };
