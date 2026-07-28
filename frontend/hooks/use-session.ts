"use client";

import type { Session } from "@supabase/supabase-js";
import { useEffect, useState } from "react";

import { supabase } from "@/lib/supabase";

export function useSession() {
  const [session, setSession] = useState<Session | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);

  useEffect(() => {
    if (!supabase) {
      setSessionLoading(false);
      return;
    }
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setSessionLoading(false);
    });
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
    });
    return () => subscription.unsubscribe();
  }, []);

  return { session, sessionLoading };
}

export function signOut() {
  return supabase?.auth.signOut();
}
