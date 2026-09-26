# Blender from the official Linux tarball instead of nixpkgs' source build.
#
# Why: GPU rendering on the 1080 Ti needs Cycles' CUDA/OptiX kernels, and
# nixpkgs only produces those via `blender.override { cudaSupport = true; }`,
# which no binary cache carries (unfree) and which drags OpenUSD, OpenSubdiv
# and OpenImageDenoise into a multi-hour local compile on every nixpkgs bump.
# The upstream tarball ships prebuilt cubins for sm_50..sm_120 (sm_60 covers
# Pascal) plus OptiX PTX, so this is a 350 MB download and a patchelf pass.
#
# autoPatchelfHook is deliberately not used: SDL3 dlopens a dozen optional
# audio/X11 libs, and this nixpkgs' auto-patchelf crashes on its ignore list.
# The upstream build targets an FHS system anyway, so the binaries get their
# interpreter fixed and the rest arrives through LD_LIBRARY_PATH, as the
# bundled libs already find each other via $ORIGIN rpaths.
#
# Update: bump `version`, then `nix store prefetch-file <url>` for the hash.
{
  lib,
  stdenv,
  fetchurl,
  makeWrapper,
  libxkbcommon,
  libglvnd,
  libGLU,
  zlib,
  libx11,
  libxext,
  libxfixes,
  libxi,
  libxrender,
  libxt,
  libxxf86vm,
  libxrandr,
  libxcursor,
  libsm,
  libice,
  wayland,
  libdecor,
  libdrm,
  dbus,
  alsa-lib,
  pulseaudio,
  vulkan-loader,
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "blender-bin";
  version = "5.2.2";

  src = fetchurl {
    url = "https://download.blender.org/release/Blender${lib.versions.majorMinor finalAttrs.version}/blender-${finalAttrs.version}-linux-x64.tar.xz";
    hash = "sha256-hAmJEnidxFDpVpfEGE+4qQrL5REcK6Su3j/stXgGoWg=";
  };

  nativeBuildInputs = [ makeWrapper ];

  # Everything the tarball does not bundle.  /run/opengl-driver/lib supplies
  # libGL from Mesa/NVIDIA and libcuda, libnvrtc and libnvoptix for Cycles.
  runtimeLibs = [
    stdenv.cc.cc.lib
    libxkbcommon
    libglvnd
    libGLU
    zlib
    libx11
    libxext
    libxfixes
    libxi
    libxrender
    libxt
    libxxf86vm
    libxrandr
    libxcursor
    libsm
    libice
    wayland
    libdecor
    libdrm
    dbus.lib
    alsa-lib
    pulseaudio
    vulkan-loader
  ];

  dontConfigure = true;
  dontBuild = true;
  # The bundled libs reference each other through $ORIGIN; leave them alone.
  dontPatchELF = true;
  dontStrip = true;

  installPhase = ''
    runHook preInstall

    mkdir -p $out/libexec/blender $out/bin $out/share/applications \
      $out/share/icons/hicolor/scalable/apps
    cp -r . $out/libexec/blender

    mv $out/libexec/blender/blender.desktop $out/share/applications/
    mv $out/libexec/blender/blender.svg $out/share/icons/hicolor/scalable/apps/
    mv $out/libexec/blender/blender-symbolic.svg $out/share/icons/hicolor/scalable/apps/

    runHook postInstall
  '';

  postFixup = ''
    interp=$(cat $NIX_CC/nix-support/dynamic-linker)
    # blender-launcher is a shell script in 5.x, not an ELF.
    for f in blender blender-thumbnailer \
      ${lib.versions.majorMinor finalAttrs.version}/python/bin/python3*; do
      patchelf --set-interpreter "$interp" $out/libexec/blender/$f
    done

    for prog in blender blender-thumbnailer; do
      makeWrapper $out/libexec/blender/$prog $out/bin/$prog \
        --prefix LD_LIBRARY_PATH : /run/opengl-driver/lib:${lib.makeLibraryPath finalAttrs.runtimeLibs}
    done
  '';

  meta = {
    description = "3D creation suite (official upstream binaries with CUDA/OptiX kernels)";
    homepage = "https://www.blender.org";
    license = lib.licenses.gpl2Plus;
    platforms = [ "x86_64-linux" ];
    mainProgram = "blender";
  };
})
