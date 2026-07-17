import React from "react";
import { View } from "react-native";
import Chat from "../chat";
import { PatientBottomNav } from "@/src/features/patient/components/PatientBottomNav";

export default function PatientMessages() {
  return (
    <View style={{ flex: 1 }}>
      <View style={{ flex: 1, marginBottom: 74 }}>
        <Chat />
      </View>
      <PatientBottomNav active="messages" />
    </View>
  );
}
