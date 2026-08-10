import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import type { Reminder } from "./db";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

export async function ensurePermissions(): Promise<boolean> {
  const existing = await Notifications.getPermissionsAsync();
  if (existing.status === "granted") {
    if (Platform.OS === "android") await ensureAndroidChannel();
    return true;
  }
  const req = await Notifications.requestPermissionsAsync();
  if (Platform.OS === "android") await ensureAndroidChannel();
  return req.status === "granted";
}

async function ensureAndroidChannel(): Promise<void> {
  await Notifications.setNotificationChannelAsync("reminders", {
    name: "Reminders",
    importance: Notifications.AndroidImportance.HIGH,
  });
}

const SCHEDULED_TAG = "reminder:";

export async function syncReminders(reminders: Reminder[]): Promise<void> {
  const scheduled = await Notifications.getAllScheduledNotificationsAsync();
  await Promise.all(
    scheduled
      .filter((n) => (n.identifier ?? "").startsWith(SCHEDULED_TAG))
      .map((n) => Notifications.cancelScheduledNotificationAsync(n.identifier)),
  );

  const now = Date.now();
  for (const r of reminders) {
    if (r.done) continue;
    if (r.remind_at <= now) continue;
    await Notifications.scheduleNotificationAsync({
      identifier: `${SCHEDULED_TAG}${r.id}`,
      content: {
        title: "Reminder",
        body: r.text,
      },
      trigger: {
        type: Notifications.SchedulableTriggerInputTypes.DATE,
        date: r.remind_at,
      },
    });
  }
}
