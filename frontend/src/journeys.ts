// What the user came to do in a chat (session_facts.journey_type, built in transform.py).
export const JOURNEYS = [
  { v: "one_way", label: "One-way flight", icon: "✈️" },
  { v: "round_trip", label: "Round-trip flight", icon: "🔁" },
  { v: "flight_hotel", label: "Flight + hotel", icon: "🧳" },
  { v: "hotel", label: "Hotel only", icon: "🏨" },
  { v: "trip_plan", label: "Trip planning", icon: "🗺️" },
  { v: "unclear", label: "Not clear yet", icon: "❔" },
];

export const JOURNEY_LABEL: Record<string, string> = Object.fromEntries(JOURNEYS.map((j) => [j.v, `${j.icon} ${j.label}`]));
