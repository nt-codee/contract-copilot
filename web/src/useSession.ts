import { useEffect, useState } from "react";
import { getSession, subscribe } from "./session";
import type { Session } from "./session";

export function useSession(): Session {
  const [s, setS] = useState(getSession);
  useEffect(() => subscribe(setS), []);
  return s;
}
