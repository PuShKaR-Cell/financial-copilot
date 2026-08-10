import React, { useEffect, useState } from "react";
import { NavigationContainer, DefaultTheme, DarkTheme } from "@react-navigation/native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { StatusBar } from "expo-status-bar";
import { useColorScheme } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import ChatScreen from "./src/screens/ChatScreen";
import MemoriesScreen from "./src/screens/MemoriesScreen";
import RemindersScreen from "./src/screens/RemindersScreen";
import SetupScreen from "./src/screens/SetupScreen";
import { ensurePermissions } from "./src/notifications";
import { isLoaded } from "./src/llm";

const Tab = createBottomTabNavigator();

export default function App() {
  const scheme = useColorScheme();
  const [ready, setReady] = useState(isLoaded());

  useEffect(() => {
    ensurePermissions().catch(() => {
      /* user can grant later */
    });
  }, []);

  return (
    <SafeAreaProvider>
      <StatusBar style="auto" />
      {ready ? (
        <NavigationContainer theme={scheme === "dark" ? DarkTheme : DefaultTheme}>
          <Tab.Navigator screenOptions={{ tabBarActiveTintColor: "#2563eb" }}>
            <Tab.Screen name="Chat" component={ChatScreen} />
            <Tab.Screen name="Memories" component={MemoriesScreen} />
            <Tab.Screen name="Reminders" component={RemindersScreen} />
          </Tab.Navigator>
        </NavigationContainer>
      ) : (
        <SetupScreen onReady={() => setReady(true)} />
      )}
    </SafeAreaProvider>
  );
}
