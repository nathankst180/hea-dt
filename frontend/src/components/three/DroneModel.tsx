// Procedural quadcopter: a body block, four diagonal arms, and a
// motor+propeller pair at the end of each arm. No external model file —
// built entirely from primitive geometry so the scene has zero asset
// dependencies. Propellers spin continuously while armed.

import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

const ARM_LENGTH = 0.55;
const ARM_ANGLES = [Math.PI / 4, (3 * Math.PI) / 4, (5 * Math.PI) / 4, (7 * Math.PI) / 4];

const BODY_COLOR = "#1c2330";
const ARM_COLOR = "#2a3344";
const ACCENT_COLOR = "#38d9ff";
const PROP_COLOR = "#0d1117";

function Propeller({ armed, rpm }: { armed: boolean; rpm: number }) {
  const propRef = useRef<THREE.Group>(null);

  useFrame((_, delta) => {
    if (!propRef.current) return;
    if (armed) {
      // Visual-only spin rate: real RPM (thousands) would just be a blur
      // at any readable frame rate, so this is scaled for legibility
      // while still responding a little to the actual RPM value.
      const speed = 12 + Math.min(rpm, 8000) / 250;
      propRef.current.rotation.y += delta * speed;
    }
  });

  return (
    <group ref={propRef} position={[0, 0.06, 0]}>
      <mesh castShadow>
        <boxGeometry args={[0.42, 0.012, 0.045]} />
        <meshStandardMaterial color={PROP_COLOR} />
      </mesh>
      <mesh castShadow rotation={[0, Math.PI / 2, 0]}>
        <boxGeometry args={[0.42, 0.012, 0.045]} />
        <meshStandardMaterial color={PROP_COLOR} />
      </mesh>
    </group>
  );
}

interface DroneModelProps {
  armed: boolean;
  motorRpm: number[];
  faulted?: boolean;
}

export function DroneModel({ armed, motorRpm, faulted = false }: DroneModelProps) {
  const bodyColor = faulted ? "#3a1c22" : BODY_COLOR;

  return (
    <group>
      {/* central body */}
      <mesh castShadow receiveShadow>
        <boxGeometry args={[0.32, 0.12, 0.32]} />
        <meshStandardMaterial color={bodyColor} metalness={0.4} roughness={0.5} />
      </mesh>
      {/* nose accent stripe, so heading is readable at a glance */}
      <mesh position={[0, 0.03, -0.19]} castShadow>
        <boxGeometry args={[0.08, 0.03, 0.06]} />
        <meshStandardMaterial color={ACCENT_COLOR} emissive={ACCENT_COLOR} emissiveIntensity={0.6} />
      </mesh>

      {ARM_ANGLES.map((angle, i) => {
        const x = Math.sin(angle) * ARM_LENGTH * 0.5;
        const z = Math.cos(angle) * ARM_LENGTH * 0.5;
        const rpm = motorRpm[i] ?? 0;
        return (
          <group key={i}>
            <mesh position={[x * 0.5, 0, z * 0.5]} rotation={[0, -angle, 0]} castShadow>
              <boxGeometry args={[0.06, 0.045, ARM_LENGTH]} />
              <meshStandardMaterial color={ARM_COLOR} metalness={0.3} roughness={0.6} />
            </mesh>
            <group position={[x, 0.02, z]}>
              <mesh castShadow>
                <cylinderGeometry args={[0.045, 0.05, 0.06, 12]} />
                <meshStandardMaterial color={ARM_COLOR} metalness={0.5} roughness={0.4} />
              </mesh>
              <Propeller armed={armed} rpm={rpm} />
            </group>
          </group>
        );
      })}
    </group>
  );
}
