export type SponsorCapability = {
  canAddTrial?: boolean;
  canAddSite?: boolean;
  canShareSchedule?: boolean;
  canManageOrganization?: boolean;
};

export type SponsorTrial = {
  id: string;
  protocolId: string;
  title: string;
  phase?: string;
  condition?: string;
  drug?: string;
  status: string;
  recruitmentStatus?: string;
  enrolled: number;
  randomized: number;
  target: number;
  sites: number;
  createdByName?: string;
  createdByRole?: string;
  createdAt?: string;
  recruitment?: RecruitmentFunnel;
};

export type RecruitmentFunnel = {
  screened: number;
  screen_fail: number;
  randomized: number;
  active: number;
  withdrawn: number;
  dropout: number;
  follow_up: number;
  completed: number;
};

export type SponsorTrialSubject = {
  id: string;
  subject_id: string;
  initials?: string;
  site: string;
  status: string;
  enrolled_at?: string;
  visits_completed: number;
  current_visit?: {
    id: string;
    visit_number?: number;
    name: string;
    status: string;
    visit_type?: string;
    scheduled_date?: string;
    window_start?: string;
    window_end?: string;
  } | null;
  deidentified: true;
};

export type SponsorTrialTeamMember = {
  id: string;
  name: string;
  role: string;
  organization?: string;
  designation?: string;
  email?: string;
  phone?: string;
};

export type SponsorTrialSite = {
  id: string;
  name: string;
  address?: string;
  city?: string;
  state?: string;
  hospital_type?: string;
  department?: string;
  status: string;
  pi_name?: string;
  pi_email?: string;
  pi_phone?: string;
  crc_name?: string;
  enrolled: number;
  target_enrollment?: number;
  enrollment_pct: number;
  visit_compliance: number;
  overdue_visits: number;
  recruitment: RecruitmentFunnel;
};

export type SponsorTrialDetail = {
  id: string;
  protocol_id: string;
  title: string;
  phase?: string;
  condition?: string;
  drug?: string;
  status: string;
  description?: string;
  sponsor_name?: string;
  duration?: string;
  target_enrollment?: number;
  recruitment_status?: string;
  ctri_number?: string;
  total_visits: number;
  site_count: number;
  enrolled_count: number;
  created_at?: string;
  created_by_name?: string;
  created_by_role?: string;
  recruitment: RecruitmentFunnel;
  sites: SponsorTrialSite[];
  subjects: SponsorTrialSubject[];
  team: SponsorTrialTeamMember[];
  visits: {
    id: string;
    name: string;
    visit_number: number;
    day_offset: number;
    window_days: number;
  }[];
  documents: {
    id: string;
    name: string;
    size: number;
    content_type?: string;
    created_at?: string;
    url: string;
  }[];
  capabilities: {
    can_add_site: boolean;
    can_manage_schedule: boolean;
    can_share: boolean;
  };
};

export type SponsorSiteTrial = {
  id: string;
  protocolId: string;
  title: string;
  phase?: string;
  condition?: string;
  drug?: string;
  status?: string;
  recruitmentStatus?: string;
  piName?: string;
};

export type SponsorSite = {
  id: string;
  name: string;
  hospital?: string;
  address?: string;
  city?: string;
  state?: string;
  department?: string;
  hospitalType?: string;
  accessType?: "full" | "restricted" | "view_only";
  status: string;
  pi?: string;
  piId?: string;
  piEmail?: string;
  piPhone?: string;
  crc?: string;
  enrolled: number;
  target: number;
  enrollmentPct: number;
  performanceScore: number;
  visitCompliance?: number;
  adherencePct?: number;
  overdueVisits?: number;
  recruitment?: RecruitmentFunnel;
  trials: SponsorSiteTrial[];
};

export type SponsorNotification = {
  id: string;
  title: string;
  message: string;
  type?: string;
  unread?: boolean;
  time?: string;
};

export type SponsorDashboard = {
  portfolio: {
    healthScore: number;
    status: string;
    activeTrials: number;
    alerts: number;
    enrolled: number;
    target: number;
    enrollmentPct: number;
    compliancePct: number;
    adherencePct: number;
    recruitment: RecruitmentFunnel;
  };
  totals: { trials: number; sites: number; subjects: number; pis: number };
  trials: SponsorTrial[];
  sites: SponsorSite[];
  recentNotifications: SponsorNotification[];
  capabilities: SponsorCapability;
};
