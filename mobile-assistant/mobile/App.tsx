import React, { useEffect } from "react";
import { NavigationContainer, DefaultTheme, DarkTheme } from "@react-navigation/native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { StatusBar } from "expo-status-bar";
import { useColorScheme } from "react-native";

import ChatScreen from "./src/screens/ChatScreen";
import MemoriesScreen from "./src/screens/MemoriesScreen";
import RemindersScreen from "./src/screens/RemindersScreen";
import { ensurePermissions } from "./src/notifications";

const Tab = createBottomTabNavigator();

export default function App() {
  const scheme = useColorScheme();

  useEffect(() => {
    ensurePermissions().catch(() => {
      /* ignore — user can grant later */
    });
  }, []);

  return (
    <NavigationContainer theme={scheme === "dark" ? DarkTheme : DefaultTheme}>
      <StatusBar style="auto" />
      <Tab.Navigator
        screenOptions={{
          tabBarActiveTintColor: "#2563eb",
        }}
      >
        <Tab.Screen
          name="Chat"
          component={ChatScreen}
          options={{ tabBarLabel: "Chat" }}
        />
        <Tab.Screen
          name="Memories"
          component={MemoriesScreen}
          options={{ tabBarLabel: "Memories" }}
        />
        <Tab.Screen
          name="Reminders"
          component={RemindersScreen}
          options={{ tabBarLabel: "Reminders" }}
        />
      </Tab.Navigator>
    </NavigationContainer>
  );
}
