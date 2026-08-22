// Converts lat/lon/alt into a local Cartesian scene position (meters),
// relative to an origin (the flight's first recorded position). Simple
// equirectangular approximation — fine at the scale of a single flight
// (tens to low hundreds of meters), not meant for large-area accuracy.
//
// Scene axes: X = East, Y = Up (altitude), Z = -North. So flying north
// moves the drone toward -Z, east moves it toward +X, and altitude is
// straight up — a fairly natural default-camera reading.

const METERS_PER_DEG_LAT = 111_320;

export function llaToScenePosition(
  lat: number,
  lon: number,
  alt: number,
  originLat: number,
  originLon: number,
): [number, number, number] {
  const metersPerDegLon = METERS_PER_DEG_LAT * Math.cos((originLat * Math.PI) / 180);
  const north = (lat - originLat) * METERS_PER_DEG_LAT;
  const east = (lon - originLon) * metersPerDegLon;
  return [east, alt, -north];
}
