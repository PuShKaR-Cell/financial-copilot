import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import type { Reminder } from "./api";

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
  if (existing.status === "granted") return true;
  const req = await Notifications.requestPermissionsAsync();
  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("reminders", {
      name: "Reminders",
      importance: Notifications.AndroidImportance.HIGH,
    });
  }
  return req.status === "granted";
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
    const remindAt = new Date(r.remind_at).getTime();
    if (Number.isNaN(remindAt) || remindAt <= now) continue;
    await Notifications.scheduleNotificationAsync({
      identifier: `${SCHEDULED_TAG}${r.id}`,
      content: {
        title: "Reminder",
        body: r.text,
      },
      trigger: {
        type: Notifications.SchedulableTriggerInputTypes.DATE,
        date: remindAt,
      },
    });
  }
}
