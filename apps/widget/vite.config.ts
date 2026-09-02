import { defineConfig } from "vite";

// Builds a single self-executing IIFE bundle. No module loading, no CSS file,
// no framework runtime for the host page to provide — the customer's site adds
// one <script> tag and the widget mounts itself into a Shadow DOM.
export default defineConfig({
  esbuild: {
    jsx: "automatic",
    jsxImportSource: "preact",
  },
  build: {
    lib: {
      entry: "src/main.ts",
      name: "OrbitWorksChat",
      formats: ["iife"],
      fileName: () => "widget.js",
    },
    // Styles are injected into the shadow root at runtime, so a separate
    // stylesheet would never apply. Keep everything in the one JS file.
    cssCodeSplit: false,
    minify: "esbuild",
    target: "es2018",
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
      },
    },
  },
  server: {
    port: 5174,
  },
});
