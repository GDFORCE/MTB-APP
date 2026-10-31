import React, { useCallback, useEffect, useState } from "react";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useAuth } from "@/src/auth/AuthContext";
import { getPatientKit, movePatientToLatest, savePatientKit, type KitPatientRecord } from "@/src/features/kit/api";
import { KitPatientView } from "@/src/features/kit/KitPatientView";

export default function KitPatientScreen() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { user } = useAuth();
  const [record, setRecord] = useState<KitPatientRecord | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setError("");
    getPatientKit(String(id)).then(setRecord)
      .catch((e: any) => setError(e?.response?.data?.detail || "Couldn't load this patient's schedule."));
  }, [id]);
  useEffect(load, [load]);

  return <KitPatientView record={record} loadError={error} canEdit={user?.role === "pi" || user?.role === "crc"}
    onBack={() => router.back()} persist={async (participant) => setRecord(await savePatientKit(String(id), participant))}
    moveToLatest={async () => setRecord(await movePatientToLatest(String(id)))} />;
}
