import { Canvas } from "@react-three/fiber";
import { Html, Line, OrbitControls } from "@react-three/drei";
import { DroneModel } from "./DroneModel";
import { formatPct } from "../../utils/format";

interface SceneProps {
  dronePosition: [number, number, number];
  droneRotation: [number, number, number];
  armed: boolean;
  motorRpm: number[];
  batteryPct: number | null;
  deviation: number;
  faulted: boolean;
  trailPoints: [number, number, number][];
}

function GroundGrid() {
  return (
    <group>
      <gridHelper args={[400, 40, "#2a3344", "#181e29"]} position={[0, 0, 0]} />
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.01, 0]} receiveShadow>
        <planeGeometry args={[400, 400]} />
        <meshStandardMaterial color="#0a0d12" />
      </mesh>
    </group>
  );
}

export function Scene({
  dronePosition,
  droneRotation,
  armed,
  motorRpm,
  batteryPct,
  deviation,
  faulted,
  trailPoints,
}: SceneProps) {
  const deviationLevel = deviation > 5 ? "var(--color-danger)" : deviation > 2 ? "var(--color-warn)" : "var(--color-nominal)";

  return (
    <Canvas shadows camera={{ position: [8, 6, 8], fov: 45 }} className="rounded-lg">
      <color attach="background" args={["#0a0d12"]} />
      <fog attach="fog" args={["#0a0d12", 40, 220]} />

      <ambientLight intensity={0.55} />
      <directionalLight position={[10, 15, 5]} intensity={1.1} castShadow shadow-mapSize={[1024, 1024]} />
      <hemisphereLight args={["#1c2c3a", "#05070a", 0.4]} />

      <GroundGrid />

      {trailPoints.length > 1 && <Line points={trailPoints} color="#1c6a80" lineWidth={1.5} transparent opacity={0.7} />}

      <group position={dronePosition} rotation={droneRotation}>
        <DroneModel armed={armed} motorRpm={motorRpm} faulted={faulted} />
        <Html distanceFactor={12} position={[0, 0.55, 0]} center occlude={false}>
          <div className="pointer-events-none flex flex-col items-center gap-0.5 whitespace-nowrap rounded-md border border-[var(--color-border)] bg-[var(--color-panel)]/90 px-2 py-1 text-[10px] leading-tight backdrop-blur-sm">
            <span className="mono-tabular font-medium text-[var(--color-text)]">{formatPct(batteryPct, 0)}</span>
            <span className="mono-tabular" style={{ color: deviationLevel }}>
              &delta;(t)={deviation.toFixed(2)}
            </span>
          </div>
        </Html>
      </group>

      <OrbitControls enableDamping dampingFactor={0.08} minDistance={2} maxDistance={120} />
    </Canvas>
  );
}
