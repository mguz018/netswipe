import React, { useState, useEffect } from 'react';
import { Heart, X, MessageCircle, Filter, Users, User, Briefcase, MapPin, Star } from 'lucide-react';

const NetSwipeApp = () => {
  // Sample professional profiles
  const initialProfiles = [
    {
      id: 1,
      name: "Sarah Chen",
      title: "Senior Product Manager",
      company: "TechFlow Inc.",
      industry: "Technology",
      location: "San Francisco, CA",
      photo: "https://images.unsplash.com/photo-1494790108755-2616b612b786?w=400&h=400&fit=crop&crop=face",
      bio: "Building the next generation of AI-powered productivity tools. Looking for technical co-founders and mentorship opportunities.",
      skills: ["Product Strategy", "AI/ML", "Team Leadership"],
      goals: "Looking for cofounders",
      experience: "8 years",
      connections: 1200
    },
    {
      id: 2,
      name: "Marcus Rodriguez",
      title: "Full Stack Engineer",
      company: "StartupXYZ",
      industry: "Technology",
      location: "Austin, TX",
      photo: "https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?w=400&h=400&fit=crop&crop=face",
      bio: "Passionate about building scalable web applications. Open to freelance opportunities and technical collaborations.",
      skills: ["React", "Node.js", "Python"],
      goals: "Freelance opportunities",
      experience: "5 years",
      connections: 850
    },
    {
      id: 3,
      name: "Dr. Emily Watson",
      title: "Research Director",
      company: "BioInnovate Labs",
      industry: "Healthcare",
      location: "Boston, MA",
      photo: "https://images.unsplash.com/photo-1559839734-2b71ea197ec2?w=400&h=400&fit=crop&crop=face",
      bio: "Leading breakthrough research in personalized medicine. Seeking partnerships with tech companies for digital health solutions.",
      skills: ["Research", "Biotech", "Team Management"],
      goals: "Partnerships",
      experience: "12 years",
      connections: 2100
    },
    {
      id: 4,
      name: "James Liu",
      title: "UX Designer",
      company: "Design Studio Pro",
      industry: "Design",
      location: "New York, NY",
      photo: "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=400&h=400&fit=crop&crop=face",
      bio: "Crafting user experiences that delight and convert. Available for design consulting and looking to mentor junior designers.",
      skills: ["UI/UX", "Design Systems", "User Research"],
      goals: "Mentorship",
      experience: "7 years",
      connections: 950
    },
    {
      id: 5,
      name: "Rachel Kim",
      title: "Marketing Director",
      company: "GrowthCorp",
      industry: "Marketing",
      location: "Los Angeles, CA",
      photo: "https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=400&h=400&fit=crop&crop=face",
      bio: "Growth hacker with a passion for data-driven marketing. Looking to connect with early-stage startups for advisory roles.",
      skills: ["Growth Marketing", "Analytics", "Brand Strategy"],
      goals: "Advisory roles",
      experience: "9 years",
      connections: 1500
    }
  ];

  const [currentUser] = useState({
    id: 0,
    name: "You",
    swipes: new Set()
  });

  const [profiles, setProfiles] = useState(initialProfiles);
  const [currentProfileIndex, setCurrentProfileIndex] = useState(0);
  const [matches, setMatches] = useState([]);
  const [currentView, setCurrentView] = useState('swipe'); // 'swipe', 'matches', 'messages'
  const [selectedMatch, setSelectedMatch] = useState(null);
  const [messages, setMessages] = useState({});
  const [swipeDirection, setSwipeDirection] = useState(null);
  const [showFilters, setShowFilters] = useState(false);
  const [filters, setFilters] = useState({
    industry: '',
    location: '',
    goals: ''
  });

  // Simulate that some profiles have already swiped right on current user
  const [mutualInterests] = useState(new Set([2, 4])); // Marcus and James are interested

  const currentProfile = profiles[currentProfileIndex];

  const handleSwipe = (direction, profileId) => {
    setSwipeDirection(direction);
    
    setTimeout(() => {
      if (direction === 'right') {
        // Check if it's a match (both swiped right)
        if (mutualInterests.has(profileId)) {
          const matchedProfile = profiles.find(p => p.id === profileId);
          setMatches(prev => [...prev, matchedProfile]);
          // Initialize empty message thread
          setMessages(prev => ({
            ...prev,
            [profileId]: []
          }));
        }
      }
      
      // Move to next profile
      setCurrentProfileIndex(prev => prev + 1);
      setSwipeDirection(null);
    }, 300);
  };

  const sendMessage = (matchId, messageText) => {
    if (!messageText.trim()) return;
    
    const newMessage = {
      id: Date.now(),
      senderId: currentUser.id,
      text: messageText,
      timestamp: new Date()
    };
    
    setMessages(prev => ({
      ...prev,
      [matchId]: [...(prev[matchId] || []), newMessage]
    }));
  };

  const filteredProfiles = profiles.filter(profile => {
    if (filters.industry && profile.industry !== filters.industry) return false;
    if (filters.location && !profile.location.includes(filters.location)) return false;
    if (filters.goals && profile.goals !== filters.goals) return false;
    return true;
  });

  const ProfileCard = ({ profile, onSwipe }) => {
    const [isDragging, setIsDragging] = useState(false);
    const [dragX, setDragX] = useState(0);

    const handleMouseDown = (e) => {
      setIsDragging(true);
      const startX = e.clientX;
      
      const handleMouseMove = (e) => {
        if (isDragging) {
          setDragX(e.clientX - startX);
        }
      };
      
      const handleMouseUp = () => {
        setIsDragging(false);
        if (Math.abs(dragX) > 100) {
          onSwipe(dragX > 0 ? 'right' : 'left', profile.id);
        }
        setDragX(0);
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
      };
      
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
    };

    return (
      <div 
        className={`relative w-full max-w-sm mx-auto bg-white rounded-2xl shadow-xl overflow-hidden cursor-grab transition-transform duration-300 ${
          swipeDirection === 'left' ? 'transform -translate-x-full rotate-12 opacity-0' :
          swipeDirection === 'right' ? 'transform translate-x-full rotate-12 opacity-0' : ''
        }`}
        style={{ 
          transform: `translateX(${dragX}px) rotate(${dragX * 0.1}deg)`,
          transition: isDragging ? 'none' : 'transform 0.3s ease-out'
        }}
        onMouseDown={handleMouseDown}
      >
        <div className="relative">
          <img 
            src={profile.photo} 
            alt={profile.name}
            className="w-full h-80 object-cover"
          />
          <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/70 to-transparent p-6 text-white">
            <h2 className="text-2xl font-bold">{profile.name}</h2>
            <p className="text-lg opacity-90">{profile.title}</p>
            <p className="text-sm opacity-75">{profile.company}</p>
          </div>
        </div>
        
        <div className="p-6 space-y-4">
          <div className="flex items-center gap-4 text-sm text-gray-600">
            <div className="flex items-center gap-1">
              <MapPin className="w-4 h-4" />
              <span>{profile.location}</span>
            </div>
            <div className="flex items-center gap-1">
              <Briefcase className="w-4 h-4" />
              <span>{profile.experience}</span>
            </div>
            <div className="flex items-center gap-1">
              <Users className="w-4 h-4" />
              <span>{profile.connections}</span>
            </div>
          </div>
          
          <p className="text-gray-700 leading-relaxed">{profile.bio}</p>
          
          <div className="space-y-3">
            <div>
              <h4 className="font-semibold text-sm text-gray-800 mb-2">Skills</h4>
              <div className="flex flex-wrap gap-2">
                {profile.skills.map((skill, index) => (
                  <span key={index} className="px-3 py-1 bg-blue-100 text-blue-800 rounded-full text-xs font-medium">
                    {skill}
                  </span>
                ))}
              </div>
            </div>
            
            <div>
              <h4 className="font-semibold text-sm text-gray-800 mb-2">Looking For</h4>
              <span className="inline-flex items-center gap-1 px-3 py-1 bg-green-100 text-green-800 rounded-full text-xs font-medium">
                <Star className="w-3 h-3" />
                {profile.goals}
              </span>
            </div>
          </div>
        </div>
      </div>
    );
  };

  const SwipeView = () => (
    <div className="flex-1 flex flex-col items-center justify-center p-4">
      {currentProfileIndex < filteredProfiles.length ? (
        <>
          <ProfileCard 
            profile={currentProfile} 
            onSwipe={handleSwipe}
          />
          
          <div className="flex gap-6 mt-8">
            <button
              onClick={() => handleSwipe('left', currentProfile.id)}
              className="w-16 h-16 bg-red-500 hover:bg-red-600 text-white rounded-full flex items-center justify-center transition-colors shadow-lg"
            >
              <X className="w-8 h-8" />
            </button>
            <button
              onClick={() => handleSwipe('right', currentProfile.id)}
              className="w-16 h-16 bg-green-500 hover:bg-green-600 text-white rounded-full flex items-center justify-center transition-colors shadow-lg"
            >
              <Heart className="w-8 h-8" />
            </button>
          </div>
          
          <div className="mt-4 text-center">
            <p className="text-gray-600">
              {filteredProfiles.length - currentProfileIndex} professionals remaining
            </p>
          </div>
        </>
      ) : (
        <div className="text-center py-12">
          <Users className="w-16 h-16 mx-auto text-gray-400 mb-4" />
          <h3 className="text-xl font-semibold text-gray-700 mb-2">No more profiles</h3>
          <p className="text-gray-500">Check back later for new professionals!</p>
          <button
            onClick={() => {
              setCurrentProfileIndex(0);
              setProfiles([...initialProfiles]);
            }}
            className="mt-4 px-6 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors"
          >
            Reset Demo
          </button>
        </div>
      )}
    </div>
  );

  const MatchesView = () => (
    <div className="flex-1 p-4">
      <div className="max-w-2xl mx-auto">
        <h2 className="text-2xl font-bold text-gray-800 mb-6">Your Matches</h2>
        {matches.length === 0 ? (
          <div className="text-center py-12">
            <Heart className="w-16 h-16 mx-auto text-gray-400 mb-4" />
            <h3 className="text-xl font-semibold text-gray-700 mb-2">No matches yet</h3>
            <p className="text-gray-500">Keep swiping to find your professional connections!</p>
          </div>
        ) : (
          <div className="grid gap-4">
            {matches.map(match => (
              <div 
                key={match.id}
                onClick={() => {
                  setSelectedMatch(match);
                  setCurrentView('messages');
                }}
                className="flex items-center gap-4 p-4 bg-white rounded-lg shadow hover:shadow-md transition-shadow cursor-pointer"
              >
                <img 
                  src={match.photo} 
                  alt={match.name}
                  className="w-16 h-16 rounded-full object-cover"
                />
                <div className="flex-1">
                  <h3 className="font-semibold text-gray-800">{match.name}</h3>
                  <p className="text-sm text-gray-600">{match.title} at {match.company}</p>
                  <p className="text-xs text-gray-500 mt-1">{match.goals}</p>
                </div>
                <MessageCircle className="w-6 h-6 text-blue-500" />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );

  const MessagesView = () => {
    const [newMessage, setNewMessage] = useState('');
    const matchMessages = messages[selectedMatch?.id] || [];

    return (
      <div className="flex-1 flex flex-col">
        <div className="bg-white border-b p-4 flex items-center gap-3">
          <button 
            onClick={() => setCurrentView('matches')}
            className="text-blue-500 hover:text-blue-600"
          >
            ← Back
          </button>
          <img 
            src={selectedMatch?.photo} 
            alt={selectedMatch?.name}
            className="w-10 h-10 rounded-full object-cover"
          />
          <div>
            <h3 className="font-semibold">{selectedMatch?.name}</h3>
            <p className="text-sm text-gray-600">{selectedMatch?.title}</p>
          </div>
        </div>
        
        <div className="flex-1 p-4 overflow-y-auto">
          {matchMessages.length === 0 ? (
            <div className="text-center py-8">
              <MessageCircle className="w-12 h-12 mx-auto text-gray-400 mb-3" />
              <p className="text-gray-500">Start the conversation!</p>
              <p className="text-sm text-gray-400 mt-2">
                You both swiped right - now's the perfect time to connect.
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {matchMessages.map(message => (
                <div 
                  key={message.id}
                  className={`flex ${message.senderId === currentUser.id ? 'justify-end' : 'justify-start'}`}
                >
                  <div className={`max-w-xs px-4 py-2 rounded-lg ${
                    message.senderId === currentUser.id 
                      ? 'bg-blue-500 text-white' 
                      : 'bg-gray-200 text-gray-800'
                  }`}>
                    {message.text}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        
        <div className="border-t p-4">
          <div className="flex gap-2">
            <input
              type="text"
              value={newMessage}
              onChange={(e) => setNewMessage(e.target.value)}
              placeholder="Type a message..."
              className="flex-1 px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
              onKeyPress={(e) => {
                if (e.key === 'Enter') {
                  sendMessage(selectedMatch.id, newMessage);
                  setNewMessage('');
                }
              }}
            />
            <button
              onClick={() => {
                sendMessage(selectedMatch.id, newMessage);
                setNewMessage('');
              }}
              className="px-6 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors"
            >
              Send
            </button>
          </div>
        </div>
      </div>
    );
  };

  const FiltersPanel = () => (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-md mx-4">
        <h3 className="text-lg font-semibold mb-4">Filters</h3>
        
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Industry</label>
            <select
              value={filters.industry}
              onChange={(e) => setFilters({...filters, industry: e.target.value})}
              className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">All Industries</option>
              <option value="Technology">Technology</option>
              <option value="Healthcare">Healthcare</option>
              <option value="Design">Design</option>
              <option value="Marketing">Marketing</option>
            </select>
          </div>
          
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Goals</label>
            <select
              value={filters.goals}
              onChange={(e) => setFilters({...filters, goals: e.target.value})}
              className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">All Goals</option>
              <option value="Looking for cofounders">Looking for cofounders</option>
              <option value="Freelance opportunities">Freelance opportunities</option>
              <option value="Partnerships">Partnerships</option>
              <option value="Mentorship">Mentorship</option>
              <option value="Advisory roles">Advisory roles</option>
            </select>
          </div>
        </div>
        
        <div className="flex gap-3 mt-6">
          <button
            onClick={() => setShowFilters(false)}
            className="flex-1 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              setCurrentProfileIndex(0);
              setShowFilters(false);
            }}
            className="flex-1 px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600"
          >
            Apply
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex flex-col">
      {/* Header */}
      <header className="bg-white shadow-sm border-b p-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <h1 className="text-2xl font-bold text-blue-600">NetSwipe</h1>
          <div className="flex items-center gap-4">
            {currentView === 'swipe' && (
              <button
                onClick={() => setShowFilters(true)}
                className="flex items-center gap-2 px-4 py-2 text-gray-600 hover:text-gray-800 transition-colors"
              >
                <Filter className="w-5 h-5" />
                Filters
              </button>
            )}
            <div className="flex items-center gap-1">
              <div className="w-8 h-8 bg-blue-500 rounded-full flex items-center justify-center">
                <User className="w-5 h-5 text-white" />
              </div>
              <span className="text-sm font-medium text-gray-700">You</span>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      {currentView === 'swipe' && <SwipeView />}
      {currentView === 'matches' && <MatchesView />}
      {currentView === 'messages' && <MessagesView />}

      {/* Bottom Navigation */}
      <nav className="bg-white border-t p-4">
        <div className="max-w-6xl mx-auto flex justify-center gap-8">
          <button
            onClick={() => setCurrentView('swipe')}
            className={`flex flex-col items-center gap-1 px-6 py-2 rounded-lg transition-colors ${
              currentView === 'swipe' 
                ? 'bg-blue-100 text-blue-600' 
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            <Users className="w-6 h-6" />
            <span className="text-xs font-medium">Discover</span>
          </button>
          
          <button
            onClick={() => setCurrentView('matches')}
            className={`flex flex-col items-center gap-1 px-6 py-2 rounded-lg transition-colors relative ${
              currentView === 'matches' 
                ? 'bg-blue-100 text-blue-600' 
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            <Heart className="w-6 h-6" />
            <span className="text-xs font-medium">Matches</span>
            {matches.length > 0 && (
              <span className="absolute -top-1 -right-1 w-5 h-5 bg-red-500 text-white text-xs rounded-full flex items-center justify-center">
                {matches.length}
              </span>
            )}
          </button>
        </div>
      </nav>

      {/* Filters Modal */}
      {showFilters && <FiltersPanel />}
    </div>
  );
};

export default NetSwipeApp;