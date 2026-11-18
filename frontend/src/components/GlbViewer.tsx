import * as React from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { Grid3X3, RotateCcw, Box } from "lucide-react";
import { Button } from "@/components/ui/button";

export function GlbViewer({
  glbUrl,
  heightClass = "h-72",
}: {
  glbUrl?: string | null;
  heightClass?: string;
}) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const [wireframe, setWireframe] = React.useState(false);
  const [showGrid, setShowGrid] = React.useState(true);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [resetSeq, setResetSeq] = React.useState(0);

  const modelGroupRef = React.useRef<THREE.Group | null>(null);
  const gridRef = React.useRef<THREE.GridHelper | null>(null);
  const cameraRef = React.useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = React.useRef<OrbitControls | null>(null);
  const defaultCameraPosRef = React.useRef<THREE.Vector3>(new THREE.Vector3(1.8, 1.3, 1.8));
  const defaultTargetRef = React.useRef<THREE.Vector3>(new THREE.Vector3(0, 0.4, 0));

  React.useEffect(() => {
    const el = containerRef.current;
    if (!el || !glbUrl) return;

    setLoading(true);
    setError(null);

    const width = el.clientWidth || 420;
    const height = el.clientHeight || 288;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#0d1117");

    const camera = new THREE.PerspectiveCamera(42, width / height, 0.01, 200);
    camera.position.copy(defaultCameraPosRef.current);
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.15;

    el.innerHTML = "";
    el.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.copy(defaultTargetRef.current);
    controlsRef.current = controls;

    // Three-point studio lighting
    const ambient = new THREE.HemisphereLight(0xdce8ff, 0x22262e, 1.15);
    scene.add(ambient);

    const key = new THREE.DirectionalLight(0xfff4e6, 2.2);
    key.position.set(3.5, 5.0, 4.0);
    scene.add(key);

    const rim = new THREE.DirectionalLight(0x88b4ff, 1.1);
    rim.position.set(-4.0, 3.0, -3.5);
    scene.add(rim);

    const grid = new THREE.GridHelper(4, 16, 0x384152, 0x1e2530);
    grid.visible = showGrid;
    gridRef.current = grid;
    scene.add(grid);

    let disposed = false;
    let rafId = 0;

    const loader = new GLTFLoader();
    loader.load(
      glbUrl,
      (gltf) => {
        if (disposed) return;
        const root = gltf.scene;
        modelGroupRef.current = root;

        const box = new THREE.Box3().setFromObject(root);
        const size = new THREE.Vector3();
        const center = new THREE.Vector3();
        box.getSize(size);
        box.getCenter(center);

        // Ground model on Y=0 centered on X/Z
        root.position.set(-center.x, -box.min.y, -center.z);
        scene.add(root);

        const maxDim = Math.max(size.x, size.y, size.z, 0.5);
        const dist = maxDim * 2.1;
        const target = new THREE.Vector3(0, size.y * 0.45, 0);
        const camPos = new THREE.Vector3(dist * 0.85, Math.max(size.y * 0.8, dist * 0.55), dist * 0.95);
        defaultTargetRef.current.copy(target);
        defaultCameraPosRef.current.copy(camPos);
        camera.position.copy(camPos);
        controls.target.copy(target);
        controls.update();
        setLoading(false);
      },
      undefined,
      (err) => {
        if (disposed) return;
        setLoading(false);
        setError(err instanceof Error ? err.message : "Failed to load GLB preview.");
      },
    );

    const ro = new ResizeObserver(() => {
      if (!containerRef.current || disposed) return;
      const w = containerRef.current.clientWidth || 420;
      const h = containerRef.current.clientHeight || 288;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });
    ro.observe(el);

    const animate = () => {
      if (disposed) return;
      rafId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    return () => {
      disposed = true;
      cancelAnimationFrame(rafId);
      ro.disconnect();
      controls.dispose();
      renderer.dispose();
      el.innerHTML = "";
    };
  }, [glbUrl, resetSeq]);

  React.useEffect(() => {
    if (gridRef.current) {
      gridRef.current.visible = showGrid;
    }
  }, [showGrid]);

  React.useEffect(() => {
    const group = modelGroupRef.current;
    if (!group) return;
    group.traverse((obj) => {
      if (obj instanceof THREE.Mesh) {
        const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
        mats.forEach((m) => {
          if (m && "wireframe" in m) {
            (m as THREE.MeshStandardMaterial).wireframe = wireframe;
          }
        });
      }
    });
  }, [wireframe]);

  if (!glbUrl) {
    return (
      <div className={`flex flex-col items-center justify-center rounded-md border border-border bg-background/60 ${heightClass}`}>
        <Box className="h-7 w-7 text-muted-foreground/60" />
        <span className="mt-2 text-xs text-muted-foreground">No interactive GLB mesh exported yet</span>
      </div>
    );
  }

  return (
    <div className="relative overflow-hidden rounded-md border border-border bg-[#0d1117]">
      <div ref={containerRef} className={`w-full ${heightClass}`} />
      <div className="absolute right-2 top-2 flex items-center gap-1 rounded-md border border-border/80 bg-card/85 p-1 backdrop-blur-sm">
        <Button
          variant={showGrid ? "secondary" : "ghost"}
          size="sm"
          onClick={() => setShowGrid((v) => !v)}
          title="Toggle floor grid"
        >
          <Grid3X3 className="h-3.5 w-3.5" />
          Grid
        </Button>
        <Button
          variant={wireframe ? "secondary" : "ghost"}
          size="sm"
          onClick={() => setWireframe((v) => !v)}
          title="Toggle wireframe"
        >
          Wire
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            if (cameraRef.current && controlsRef.current) {
              cameraRef.current.position.copy(defaultCameraPosRef.current);
              controlsRef.current.target.copy(defaultTargetRef.current);
              controlsRef.current.update();
            } else {
              setResetSeq((n) => n + 1);
            }
          }}
          title="Reset camera"
        >
          <RotateCcw className="h-3.5 w-3.5" />
        </Button>
      </div>
      {loading ? (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-background/50 text-xs text-muted-foreground">
          Loading 3D mesh...
        </div>
      ) : null}
      {error ? (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-background/80 px-4 text-center text-xs text-rose-300">
          {error}
        </div>
      ) : null}
    </div>
  );
}
